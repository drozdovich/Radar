"""Optional pinned TDLib for Radar, using the existing account and profile lock.

The installed MCP and its original database are not modified. The encrypted copy
is deliberately never opened with a fallback library after it has been migrated.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import __version__

SUPPORTED_VERSION = '1.8.67'
SOURCE_COMMIT = 'd1085f9cebc5a62379991ae1652673954f229c1f'


def load_runtime(path: Path):
    if not path.exists():
        return None
    config = json.loads(path.read_text())
    if config.get('version') != SUPPORTED_VERSION or config.get('source_commit') != SOURCE_COMMIT:
        raise ValueError('Unverified Telegram runtime version')
    for key in ('library', 'database_directory', 'files_directory'):
        target = Path(config[key])
        if not target.is_absolute() or not target.exists():
            raise ValueError(f'Pinned Telegram runtime {key} is unavailable; no fallback used')
    actual = hashlib.sha256(Path(config['library']).read_bytes()).hexdigest()
    if actual != config.get('library_sha256'):
        raise ValueError('Pinned Telegram library hash changed; no fallback used')
    return config


def pinned_session(policy, profile, config):
    # Lazy import: the existing MCP source directory is added by TdlibSource.
    from telegram_search_mcp import tdlib_backend as backend

    class PinnedSession(backend.TdlibSession):
        def open(self):
            with self._state_lock:
                if self.client is not None:
                    return
                backend.ensure_runtime_layout(self.profile)
                self._acquire_profile_lock()
                try:
                    api_hash = backend.get_secret('api_hash', self.profile)
                    encoded_key = backend.get_secret('database_key', self.profile)
                    if not api_hash or not encoded_key:
                        raise backend.SetupRequiredError('Existing Telegram credentials are unavailable')
                    parameters = backend.TdlibParameters(
                        api_id=self.policy.api_id, api_hash=api_hash,
                        database_directory=config['database_directory'],
                        files_directory=config['files_directory'],
                        database_encryption_key=backend._decode_database_key(encoded_key),
                        use_secret_chats=False,
                        device_model='Codex local read-only Telegram Radar',
                        application_version=__version__,
                    )
                    self.transport = backend.CtypesTdJsonTransport(
                        library_path=config['library'], log_verbosity=0)
                    self.client = backend.TdClient(self.transport)
                    machine = backend.AuthorizationMachine(parameters, backend.TdlibSchema.CURRENT)
                    self.authorization = backend.AuthorizationController(self.client, machine)
                    self.authorization.begin()
                except BaseException:
                    self._close_components()
                    raise

        def verify_runtime_version(self, timeout=10.0):
            response = self.request({'@type': 'getOption', 'name': 'version'}, timeout=timeout)
            if response.get('@type') != 'optionValueString' or response.get('value') != config['version']:
                raise RuntimeError('Pinned Telegram runtime version does not match its manifest')

    return PinnedSession(policy, profile)
