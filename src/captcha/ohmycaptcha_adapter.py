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
_NAMESPACE_COMPLETE_ATTR = '__price_watcher_vendor_complete__'
_NAMESPACE_ROOT_ATTR = '__price_watcher_vendor_root__'
_NAMESPACE_COMPLETE_VALUE = f'pinned:{PINNED_UPSTREAM_COMMIT}'
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
        namespace = _vendor_namespace(self._vendor_root)
        scripts: dict[str, str] = {}
        contract_failed = False
        try:
            _load_vendor_package(self._vendor_root)
            for public_name, contract in _SCRIPT_CONTRACTS.items():
                module_name, private_name, markers = contract
                module = importlib.import_module(
                    f'{namespace}.{module_name}'
                )
                script = getattr(module, private_name)
                if not isinstance(script, str) or not all(
                    marker in script for marker in markers
                ):
                    contract_failed = True
                    break
                scripts[public_name] = script
            if not contract_failed:
                _mark_namespace_complete(
                    namespace,
                    self._vendor_root / 'src',
                )
        except BaseException as error:
            _discard_namespace(namespace)
            if not isinstance(error, Exception):
                raise
            contract_failed = True
        if contract_failed:
            _discard_namespace(namespace)
            raise _safe_contract_error()
        return scripts

    def _validate_metadata(self) -> None:
        metadata: str | None = None
        try:
            metadata = (self._vendor_root / 'UPSTREAM.md').read_text(
                encoding='utf-8'
            )
        except OSError:
            pass
        if metadata is None:
            raise _safe_contract_error()
        pin_marker = f'Imported commit: `{PINNED_UPSTREAM_COMMIT}`'
        if pin_marker not in metadata:
            raise _safe_contract_error()


def _load_vendor_package(vendor_root: Path) -> str:
    package_directory = vendor_root / 'src'
    package_file = package_directory / '__init__.py'
    namespace = _vendor_namespace(vendor_root)
    if namespace in sys.modules:
        cached = sys.modules.get(namespace)
        if _cached_namespace_is_complete(
            cached,
            namespace,
            package_directory,
            package_file,
        ):
            return namespace
        _discard_namespace(namespace)

    spec = importlib.util.spec_from_file_location(
        namespace,
        package_file,
        submodule_search_locations=[str(package_directory)],
    )
    if spec is None or spec.loader is None:
        raise ImportError('vendor package spec is unavailable')
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[namespace] = module
        spec.loader.exec_module(module)
    except BaseException:
        _discard_namespace(namespace)
        raise
    setattr(module, _NAMESPACE_ROOT_ATTR, str(package_directory.resolve()))
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
        sys.modules.pop(name, None)


def _mark_namespace_complete(
    namespace: str,
    package_directory: Path,
) -> None:
    module = sys.modules.get(namespace)
    if not isinstance(module, ModuleType):
        raise ImportError('vendor package root is unavailable')
    setattr(module, _NAMESPACE_ROOT_ATTR, str(package_directory.resolve()))
    setattr(module, _NAMESPACE_COMPLETE_ATTR, _NAMESPACE_COMPLETE_VALUE)


def _cached_namespace_is_complete(
    module: object,
    namespace: str,
    package_directory: Path,
    package_file: Path,
) -> bool:
    if not isinstance(module, ModuleType):
        return False
    if (
        getattr(module, _NAMESPACE_COMPLETE_ATTR, None)
        != _NAMESPACE_COMPLETE_VALUE
        or getattr(module, _NAMESPACE_ROOT_ATTR, None)
        != str(package_directory.resolve())
    ):
        return False
    spec = module.__spec__
    if spec is None or spec.origin is None:
        return False
    if Path(spec.origin).resolve() != package_file.resolve():
        return False
    locations = spec.submodule_search_locations
    if locations is None or tuple(
        Path(location).resolve() for location in locations
    ) != (package_directory.resolve(),):
        return False
    return _cached_private_contracts_match(namespace)


def _cached_private_contracts_match(namespace: str) -> bool:
    for module_name, private_name, markers in _SCRIPT_CONTRACTS.values():
        module = sys.modules.get(f'{namespace}.{module_name}')
        if not isinstance(module, ModuleType):
            return False
        script = getattr(module, private_name, None)
        if not isinstance(script, str) or not all(
            marker in script for marker in markers
        ):
            return False
    return True


def _safe_contract_error() -> VendorContractError:
    return VendorContractError('pinned vendor contract is unavailable')
