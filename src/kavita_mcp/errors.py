"""Errors whose message is shown to the MCP client as is."""


class KavitaMcpError(Exception):
    """Base for every error with a user-facing message."""


class ConfigError(KavitaMcpError):
    """The environment is missing a setting or has an invalid one."""
