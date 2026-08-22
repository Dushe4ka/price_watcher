"""Narrow adapter over the immutable OhMyCaptcha vendor snapshot."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Final, Mapping


PINNED_UPSTREAM_COMMIT: Final = (
    '0b543d5436700fa3455e634583e2642a8a64159f'
)

_DEFAULT_VENDOR_ROOT = (
    Path(__file__).resolve().parents[2] / 'vendor' / 'ohmycaptcha'
)
_NAMESPACE_PREFIX = '_price_watcher_ohmycaptcha_vendor_'
_SCRIPT_CONTRACTS = {
    'recaptcha_v2_extract': (
        'services.recaptcha_v2',
        '_EXTRACT_TOKEN_JS',
        ('#g-recaptcha-response', 'grecaptcha?.enterprise'),
    ),
    'recaptcha_v3_execute': (
        'services.recaptcha_v3',
        '_EXECUTE_JS',
        ('gr.execute(key, {action})', 'document.head.appendChild(script)'),
    ),
    'hcaptcha_extract': (
        'services.hcaptcha',
        '_EXTRACT_HCAPTCHA_TOKEN_JS',
        ('[name="h-captcha-response"]', 'window.hcaptcha.getResponse'),
    ),
    'turnstile_extract': (
        'services.turnstile',
        '_EXTRACT_TURNSTILE_TOKEN_JS',
        (
            '[name="cf-turnstile-response"]',
            'window.turnstile.getResponse',
        ),
    ),
}


class VendorContractError(RuntimeError):
    """The reviewed private vendor contract is missing or has drifted."""


class OhMyCaptchaAdapter:
    """Expose reviewed vendor primitives without exposing solver objects."""

    __slots__ = ('_scripts', '_vendor_root')

    def __init__(self, vendor_root: Path = _DEFAULT_VENDOR_ROOT) -> None:
        self._vendor_root = Path(vendor_root).resolve()
        self._scripts: Mapping[str, str] | None = None

    def vendor_scripts(self) -> Mapping[str, str]:
        """Return immutable reviewed JavaScript primitives from the pin."""
        if self._scripts is None:
            self._scripts = MappingProxyType(self._load_vendor_scripts())
        return self._scripts

    def __repr__(self) -> str:
        return 'OhMyCaptchaAdapter(vendor_snapshot=pinned)'

    def _load_vendor_scripts(self) -> dict[str, str]:
        self._validate_metadata()
        namespace = _load_vendor_package(self._vendor_root)
        scripts: dict[str, str] = {}
        try:
            for public_name, contract in _SCRIPT_CONTRACTS.items():
                module_name, private_name, markers = contract
                module = importlib.import_module(
                    f'{namespace}.{module_name}'
                )
                script = getattr(module, private_name)
                if not isinstance(script, str) or not all(
                    marker in script for marker in markers
                ):
                    raise VendorContractError
                scripts[public_name] = script
        except Exception:
            _discard_namespace(namespace)
            raise VendorContractError(
                'pinned vendor contract is unavailable'
            ) from None
        return scripts

    def _validate_metadata(self) -> None:
        try:
            metadata = (self._vendor_root / 'UPSTREAM.md').read_text(
                encoding='utf-8'
            )
        except OSError:
            raise VendorContractError(
                'pinned vendor contract is unavailable'
            ) from None
        pin_marker = f'Imported commit: `{PINNED_UPSTREAM_COMMIT}`'
        if pin_marker not in metadata:
            raise VendorContractError(
                'pinned vendor contract is unavailable'
            )


def _load_vendor_package(vendor_root: Path) -> str:
    package_directory = vendor_root / 'src'
    package_file = package_directory / '__init__.py'
    namespace = _vendor_namespace(vendor_root)
    if namespace in sys.modules:
        return namespace

    spec = importlib.util.spec_from_file_location(
        namespace,
        package_file,
        submodule_search_locations=[str(package_directory)],
    )
    if spec is None or spec.loader is None:
        raise VendorContractError(
            'pinned vendor contract is unavailable'
        )
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[namespace] = module
        spec.loader.exec_module(module)
    except Exception:
        _discard_namespace(namespace)
        raise VendorContractError(
            'pinned vendor contract is unavailable'
        ) from None
    return namespace


def _vendor_namespace(vendor_root: Path) -> str:
    digest = hashlib.sha256(str(vendor_root).encode('utf-8')).hexdigest()
    return f'{_NAMESPACE_PREFIX}{digest[:16]}'


def _discard_namespace(namespace: str) -> None:
    names = tuple(
        name
        for name in sys.modules
        if name == namespace or name.startswith(f'{namespace}.')
    )
    for name in names:
        module = sys.modules.get(name)
        if isinstance(module, ModuleType):
            sys.modules.pop(name, None)
