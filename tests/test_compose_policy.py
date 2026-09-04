"""Rendered Compose and image policy for persistent browser fallbacks."""

from __future__ import annotations

import json
import unittest

import yaml

from scripts.verify_compose import (
    BASE_COMPOSE_FILE,
    BROWSER_PROFILE_TARGET,
    CI_WORKFLOW_FILE,
    LOCAL_COMPOSE_FILE,
    PRODUCTION_COMPOSE_FILE,
    REPOSITORY_ROOT,
    SECCOMP_PROFILE_FILE,
    check_policy,
    published_ports,
    render_compose,
    shared_browser_stage,
)


def read_repository_text(relative_path: str) -> str:
    """Return one repository file as text, for image policy assertions."""
    return (REPOSITORY_ROOT / relative_path).read_text(encoding='utf-8')


class ProductionComposePolicyTests(unittest.TestCase):
    """The production overlay isolates browser state and stays private."""

    def test_production_services_have_isolated_profile_volumes(self) -> None:
        compose = render_compose('docker-compose.production.yml')
        api_mounts = compose['services']['api']['volumes']
        bot_mounts = compose['services']['bot']['volumes']
        self.assertIn('api_browser_profiles:/data/browser-profiles',
                      api_mounts)
        self.assertIn('bot_browser_profiles:/data/browser-profiles',
                      bot_mounts)
        self.assertNotEqual(api_mounts, bot_mounts)

    def test_production_uses_one_worker_and_no_public_browser_port(
        self,
    ) -> None:
        compose = render_compose('docker-compose.production.yml')
        self.assertEqual(
            '1',
            compose['services']['api']['environment']['WEB_CONCURRENCY'],
        )
        self.assertNotIn('ports', compose['services']['bot'])

    def test_bot_also_pins_one_worker_for_its_browser_manager(self) -> None:
        """The bot boots the same profile guard, so it needs the same value."""
        compose = render_compose(PRODUCTION_COMPOSE_FILE)
        environment = compose['services']['bot']['environment']
        self.assertEqual('1', environment['WEB_CONCURRENCY'])

    def test_profile_volumes_are_declared_and_never_shared(self) -> None:
        compose = render_compose(PRODUCTION_COMPOSE_FILE)
        declared = compose['volumes']
        self.assertIn('api_browser_profiles', declared)
        self.assertIn('bot_browser_profiles', declared)
        self.assertNotIn('ozon_profile', declared)

    def test_each_role_writes_its_own_profile_root(self) -> None:
        """Task 8 keys profiles by (role, marketplace) under one root."""
        compose = render_compose(PRODUCTION_COMPOSE_FILE)
        api_environment = compose['services']['api']['environment']
        bot_environment = compose['services']['bot']['environment']
        self.assertEqual('api', api_environment['MARKETPLACE_RUNTIME_ROLE'])
        self.assertEqual('bot', bot_environment['MARKETPLACE_RUNTIME_ROLE'])
        self.assertEqual(
            BROWSER_PROFILE_TARGET,
            api_environment['BROWSER_PROFILE_ROOT'],
        )
        self.assertEqual(
            BROWSER_PROFILE_TARGET,
            bot_environment['BROWSER_PROFILE_ROOT'],
        )

    def test_browser_services_run_as_a_non_root_user(self) -> None:
        compose = render_compose(PRODUCTION_COMPOSE_FILE)
        for name in ('api', 'bot'):
            with self.subTest(service=name):
                user = compose['services'][name]['user']
                self.assertNotIn(user, ('root', '0', '0:0'))
                self.assertFalse(user.startswith('0:'))

    def test_browser_services_size_shared_memory_for_chromium(self) -> None:
        compose = render_compose(PRODUCTION_COMPOSE_FILE)
        for name in ('api', 'bot'):
            with self.subTest(service=name):
                self.assertIn('shm_size', compose['services'][name])

    def test_browser_services_raise_the_open_file_descriptor_limit(
        self,
    ) -> None:
        """Docker's 1024 default is too low for a headed Chromium — a live
        run against a real page showed the CDP `Target.createTarget` call
        failing outright once the container's fd usage climbed."""
        compose = render_compose(PRODUCTION_COMPOSE_FILE)
        for name in ('api', 'bot'):
            with self.subTest(service=name):
                nofile = compose['services'][name]['ulimits']['nofile']
                self.assertGreaterEqual(nofile['soft'], 65536)
                self.assertGreaterEqual(nofile['hard'], 65536)

    def test_browser_services_apply_the_playwright_seccomp_profile(
        self,
    ) -> None:
        compose = render_compose(PRODUCTION_COMPOSE_FILE)
        for name in ('api', 'bot'):
            with self.subTest(service=name):
                options = compose['services'][name]['security_opt']
                self.assertTrue(
                    any(
                        option.startswith('seccomp')
                        and SECCOMP_PROFILE_FILE in option
                        for option in options
                    ),
                    options,
                )

    def test_no_service_publishes_a_browser_control_port(self) -> None:
        compose = render_compose(PRODUCTION_COMPOSE_FILE)
        for name, ports in published_ports(compose).items():
            with self.subTest(service=name):
                self.assertNotIn(name, ('api', 'bot', 'db'))
                for port in ports:
                    self.assertNotIn('9222', port)
                    self.assertNotIn('9229', port)

    def test_policy_checker_accepts_the_shipped_production_render(
        self,
    ) -> None:
        compose = render_compose(BASE_COMPOSE_FILE, PRODUCTION_COMPOSE_FILE)
        self.assertEqual([], check_policy(compose))


class LocalComposePolicyTests(unittest.TestCase):
    """The local overlay stays convenient without exposing the host."""

    def test_base_compose_publishes_no_host_ports(self) -> None:
        compose = render_compose(BASE_COMPOSE_FILE)
        self.assertEqual({}, published_ports(compose))

    def test_local_overlay_binds_every_published_port_to_loopback(
        self,
    ) -> None:
        compose = render_compose(LOCAL_COMPOSE_FILE)
        mappings = published_ports(compose)
        self.assertNotEqual({}, mappings)
        for name, ports in mappings.items():
            with self.subTest(service=name):
                for port in ports:
                    self.assertTrue(port.startswith('127.0.0.1:'), port)

    def test_local_overlay_keeps_profile_volumes_isolated(self) -> None:
        compose = render_compose(LOCAL_COMPOSE_FILE)
        api_mounts = compose['services']['api']['volumes']
        bot_mounts = compose['services']['bot']['volumes']
        self.assertNotEqual(api_mounts, bot_mounts)
        self.assertEqual([], check_policy(compose))


class BrowserImagePolicyTests(unittest.TestCase):
    """Both images build the same browser runtime and drop root."""

    def setUp(self) -> None:
        self.api_dockerfile = read_repository_text('Dockerfile.api')
        self.bot_dockerfile = read_repository_text('Dockerfile.bot')

    def test_both_images_share_one_browser_runtime_stage(self) -> None:
        api_stage = shared_browser_stage(self.api_dockerfile)
        bot_stage = shared_browser_stage(self.bot_dockerfile)
        self.assertNotEqual('', api_stage)
        self.assertEqual(api_stage, bot_stage)

    def test_shared_stage_installs_browsers_and_a_virtual_display(
        self,
    ) -> None:
        stage = shared_browser_stage(self.api_dockerfile)
        self.assertIn('playwright install chromium', stage)
        self.assertIn('patchright install chrome', stage)
        self.assertIn('xvfb', stage)
        self.assertIn('tini', stage)

    def test_both_images_use_tini_as_init_and_a_virtual_display(self) -> None:
        for name, content in (
            ('Dockerfile.api', self.api_dockerfile),
            ('Dockerfile.bot', self.bot_dockerfile),
        ):
            with self.subTest(dockerfile=name):
                self.assertIn('ENTRYPOINT ["tini", "--"]', content)
                self.assertIn('xvfb-run', content)

    def test_both_images_end_as_a_non_root_user(self) -> None:
        for name, content in (
            ('Dockerfile.api', self.api_dockerfile),
            ('Dockerfile.bot', self.bot_dockerfile),
        ):
            with self.subTest(dockerfile=name):
                users = [
                    line.split(maxsplit=1)[1].strip()
                    for line in content.splitlines()
                    if line.startswith('USER ')
                ]
                self.assertTrue(users, 'image never drops root')
                self.assertNotIn(users[-1], ('root', '0'))

    def test_images_install_only_the_main_runtime_requirements(self) -> None:
        for name, content in (
            ('Dockerfile.api', self.api_dockerfile),
            ('Dockerfile.bot', self.bot_dockerfile),
        ):
            with self.subTest(dockerfile=name):
                instructions = '\n'.join(
                    line
                    for line in content.splitlines()
                    if not line.lstrip().startswith('#')
                )
                self.assertIn('-r requirements.txt', instructions)
                self.assertNotIn('vendor', instructions)

    def test_main_browser_pins_stay_distinct_from_vendor_pins(self) -> None:
        main_pins = self._pins('requirements.txt')
        vendor_pins = self._pins('vendor/ohmycaptcha/requirements.txt')
        self.assertEqual('1.53.0', main_pins['playwright'])
        self.assertEqual('1.61.2', main_pins['patchright'])
        self.assertEqual('1.49.1', vendor_pins['playwright'])
        self.assertNotEqual(main_pins['playwright'], vendor_pins['playwright'])

    def _pins(self, relative_path: str) -> dict[str, str]:
        pins: dict[str, str] = {}
        for line in read_repository_text(relative_path).splitlines():
            entry = line.strip()
            if not entry or entry.startswith('#') or '==' not in entry:
                continue
            name, _, version = entry.partition('==')
            pins[name.strip().lower()] = version.strip()
        return pins


class SeccompProfilePolicyTests(unittest.TestCase):
    """The vendored profile matches Playwright's documented recommendation."""

    def setUp(self) -> None:
        self.profile = json.loads(read_repository_text(SECCOMP_PROFILE_FILE))

    def test_profile_denies_unlisted_syscalls_by_default(self) -> None:
        self.assertEqual('SCMP_ACT_ERRNO', self.profile['defaultAction'])

    def test_profile_allows_creating_user_namespaces(self) -> None:
        allowed = {
            name
            for rule in self.profile['syscalls']
            if rule['action'] == 'SCMP_ACT_ALLOW'
            for name in rule['names']
        }
        self.assertLessEqual({'clone', 'setns', 'unshare'}, allowed)

    def test_profile_ships_with_an_explanation(self) -> None:
        readme = read_repository_text('infra/playwright/README.md')
        self.assertIn('seccomp', readme.lower())
        self.assertIn('clone', readme)


class ContinuousIntegrationPolicyTests(unittest.TestCase):
    """CI runs the controlled suite without credentials or live traffic."""

    def setUp(self) -> None:
        self.workflow = yaml.safe_load(read_repository_text(CI_WORKFLOW_FILE))
        self.raw = read_repository_text(CI_WORKFLOW_FILE)

    def test_workflow_runs_the_controlled_unittest_suite(self) -> None:
        self.assertIn('unittest discover -s tests -t .', self.raw)
        self.assertIn('PYTHON_DOTENV_DISABLED', self.raw)

    def test_workflow_never_enables_live_marketplace_tests(self) -> None:
        self.assertNotIn('LIVE_MARKETPLACE_TESTS: ', self.raw)
        self.assertNotIn("LIVE_MARKETPLACE_TESTS=1", self.raw)
        self.assertNotIn('secrets.', self.raw)

    def test_workflow_provides_node_for_the_in_page_contract_tests(
        self,
    ) -> None:
        """Task 11's Ozon fetch contract tests skip silently without node."""
        steps = [
            step
            for job in self.workflow['jobs'].values()
            for step in job['steps']
        ]
        self.assertTrue(
            any(
                str(step.get('uses', '')).startswith('actions/setup-node')
                for step in steps
            ),
            'CI must install node so the contract tests cannot skip',
        )

    def test_workflow_verifies_compose_policy_without_a_daemon(self) -> None:
        self.assertIn('scripts/verify_compose.py', self.raw)


if __name__ == '__main__':
    unittest.main()
