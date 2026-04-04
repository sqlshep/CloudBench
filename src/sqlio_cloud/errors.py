"""User-friendly error handling with cloud-specific remediation advice."""

from __future__ import annotations

ERROR_REMEDIATION: list[tuple[str, str]] = [
    ("timeout expired", (
        "The query exceeded the timeout limit.\n"
        "This usually means the database tier is too small for this workload.\n"
        "Try:\n"
        "  - Upgrading to a higher compute tier\n"
        "  - Reducing the scale factor (use 'smoke' profile)\n"
        "  - Increasing the query timeout in config"
    )),
    ("login failed", (
        "Authentication failed.\n"
        "  - Double-check username and password\n"
        "  - For Azure SQL: username format is 'user@servername'\n"
        "  - For IAM auth: ensure your token hasn't expired"
    )),
    ("does not allow remote connections", (
        "The server rejected the connection.\n"
        "  - Add your IP to the database firewall rules\n"
        "  - Azure: Portal -> SQL Server -> Networking -> Add client IP\n"
        "  - AWS:   Check the Security Group inbound rules\n"
        "  - GCP:   Check Authorized Networks in Cloud SQL"
    )),
    ("no pg_hba.conf entry", (
        "PostgreSQL rejected the connection (host not authorized).\n"
        "  - Add your IP to the pg_hba.conf allowlist\n"
        "  - For cloud: check firewall / authorized networks"
    )),
    ("ssl", (
        "SSL/TLS negotiation failed.\n"
        "  - Ensure the server has SSL enabled\n"
        "  - Try setting ssl_mode to 'require' in your config\n"
        "  - For Azure SQL, SSL is always required"
    )),
    ("could not connect to server", (
        "Cannot reach the database server.\n"
        "  - Check the hostname is correct\n"
        "  - Verify DNS resolution works: nslookup <hostname>\n"
        "  - Ensure the port is open in your firewall\n"
        "  - For private endpoints: check VPN/VNet connectivity"
    )),
    ("connection refused", (
        "Connection refused by the server.\n"
        "  - Verify the database is running\n"
        "  - Check the port number is correct\n"
        "  - Ensure nothing is blocking the connection (firewall, NSG)"
    )),
    ("password authentication failed", (
        "Wrong password.\n"
        "  - Double-check the password (copy-paste to avoid typos)\n"
        "  - Reset the password if unsure\n"
        "  - Check if the user account is locked"
    )),
    ("permission denied", (
        "Insufficient privileges.\n"
        "  - The user needs CREATE TABLE, INSERT, SELECT, UPDATE, DELETE permissions\n"
        "  - Grant with: GRANT ALL ON SCHEMA public TO <user>;\n"
        "  - For Azure SQL: ALTER ROLE db_owner ADD MEMBER <user>;"
    )),
    ("disk", (
        "Storage-related error.\n"
        "  - Check if the database has hit its storage limit\n"
        "  - Increase the storage allocation in your cloud provider console\n"
        "  - Try a smaller scale factor"
    )),
    ("memory", (
        "Out of memory on the database server.\n"
        "  - Upgrade to a higher compute tier with more RAM\n"
        "  - Reduce concurrency (fewer threads)\n"
        "  - Use a smaller scale factor"
    )),
]


def friendly_error(exc: Exception) -> str:
    """Match an exception to actionable remediation advice."""
    msg = str(exc).lower()
    for pattern, advice in ERROR_REMEDIATION:
        if pattern in msg:
            return advice
    return f"Unexpected error: {exc}\n\nIf this persists, please file an issue with the full traceback."


def validate_port(text: str) -> bool | str:
    try:
        p = int(text)
        if 1 <= p <= 65535:
            return True
        return "Port must be between 1 and 65535"
    except ValueError:
        return "Port must be a number"
