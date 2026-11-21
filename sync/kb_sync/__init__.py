"""kb-sync: keep Drive, AppFlowy and local research in step with LightRAG (spec 002).

Shared error types. Each carries the exit code the CLI returns (contracts/kb-cli.md).
"""


class KbSyncError(Exception):
    """Base error. The CLI prints `ERROR: <message>` to stderr and exits with `exit_code`."""

    exit_code = 1

    def __init__(self, message, exit_code=None):
        super().__init__(message)
        self.message = message
        if exit_code is not None:
            self.exit_code = exit_code

    def __str__(self):
        return self.message


class ConfigError(KbSyncError):
    """A required .env key is missing or a value is invalid. Exit 3."""

    exit_code = 3

    def __init__(self, key, why=None):
        self.key = key
        msg = f"invalid .env key {key}: {why}" if why else f"missing .env key {key}"
        super().__init__(msg)


class ServerDown(KbSyncError):
    """LightRAG is not reachable. Exit 3."""

    exit_code = 3

    def __init__(self, url):
        self.url = url
        super().__init__(f"LightRAG server is not running at {url}. Start it with ./scripts/start.sh")


class AuthExpired(KbSyncError):
    """A cloud sign-in is missing, expired or revoked. Exit 3 (FR-018)."""

    exit_code = 3

    def __init__(self, source, renew):
        self.source = source
        self.renew = renew
        super().__init__(f"{source} sign-in expired. Renew it with scripts/kb.sh auth {renew}")


class Refused(KbSyncError):
    """Refused for safety, for example held removals. Exit 4."""

    exit_code = 4
