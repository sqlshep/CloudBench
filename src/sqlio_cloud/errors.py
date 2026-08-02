"""User-friendly error handling with cloud-specific remediation advice."""

from __future__ import annotations

ERROR_REMEDIATION: list[tuple[str, str]] = [
    # --- Connection / onboarding failures (checked first — most common) ---
    ("is not allowed to access the server", (
        "Your IP is blocked by the Azure SQL firewall.\n"
        "  - Azure Portal -> your SQL *server* (not the database) -> Security -> Networking\n"
        "  - Under 'Firewall rules', click '+ Add your client IPv4 address', then Save\n"
        "  - For a throwaway test server you can allow 0.0.0.0 - 255.255.255.255\n"
        "  - Firewall changes take 1-2 minutes to propagate"
    )),
    ("sp_set_firewall_rule", (
        "Your IP is blocked by the Azure SQL firewall.\n"
        "  - Add the Data Bench IP (shown on the connection page) to the server's firewall rules\n"
        "  - Azure Portal -> SQL server -> Security -> Networking -> Add a firewall rule"
    )),
    ("40613", (
        "The Azure SQL database is currently unavailable (error 40613).\n"
        "  - It may be paused (serverless tier) or mid-failover -- wait ~60s and retry\n"
        "  - Confirm the database is 'Online' in the Azure Portal"
    )),
    ("getaddrinfo", (
        "The hostname could not be resolved (DNS lookup failed).\n"
        "  - Double-check the hostname for typos\n"
        "  - Verify it resolves: run 'nslookup <hostname>' in a terminal\n"
        "  - Make sure the machine running Data Bench has internet/DNS access"
    )),
    ("name or service not known", (
        "The hostname could not be resolved (DNS lookup failed).\n"
        "  - Double-check the hostname for typos\n"
        "  - Verify it resolves: run 'nslookup <hostname>' in a terminal"
    )),
    ("could not translate host name", (
        "The hostname could not be resolved (DNS lookup failed).\n"
        "  - Double-check the hostname for typos\n"
        "  - Verify it resolves: run 'nslookup <hostname>' in a terminal"
    )),
    ("nodename nor servname", (
        "The hostname could not be resolved (DNS lookup failed).\n"
        "  - Double-check the hostname for typos\n"
        "  - Verify it resolves: run 'nslookup <hostname>' in a terminal"
    )),
    ("network is unreachable", (
        "The network route to the database is unreachable.\n"
        "  - The machine running Data Bench has no path to the database host/port\n"
        "  - Check your local internet connection and any VPN requirement\n"
        "  - For private endpoints, run Data Bench inside the same VNet/VPC"
    )),
    ("no route to host", (
        "The network route to the database is unreachable.\n"
        "  - Check your local internet connection and any VPN/VNet requirement\n"
        "  - Verify the host and port are correct and publicly reachable"
    )),
    ("20009", (
        "Could not reach the SQL Server / Azure SQL endpoint (DB-Lib error 20009).\n"
        "  - Verify the hostname and port (Azure SQL DB: 1433, Managed Instance: 3342)\n"
        "  - Add your IP to the server's firewall rules\n"
        "  - Confirm the machine running Data Bench can reach the host on that port:\n"
        "    'nc -vz <hostname> <port>' (macOS/Linux) or 'Test-NetConnection <hostname> -Port <port>' (Windows)"
    )),
    ("adaptive server is unavailable", (
        "Could not reach the SQL Server / Azure SQL endpoint.\n"
        "  - Verify the hostname and port (Azure SQL DB: 1433, Managed Instance: 3342)\n"
        "  - Add your IP to the server's firewall rules\n"
        "  - Confirm the host is reachable on that port from this machine"
    )),
    ("login timeout expired", (
        "Timed out while opening the connection.\n"
        "  - The host/port is likely unreachable (firewall, NSG, or wrong port)\n"
        "  - Verify connectivity: 'nc -vz <hostname> <port>' or 'Test-NetConnection <hostname> -Port <port>'\n"
        "  - Add your IP to the database firewall rules"
    )),
    ("connection timed out", (
        "Timed out while opening the connection.\n"
        "  - The host/port is likely unreachable (firewall, NSG, or wrong port)\n"
        "  - Verify connectivity: 'nc -vz <hostname> <port>' or 'Test-NetConnection <hostname> -Port <port>'\n"
        "  - Add your IP to the database firewall rules"
    )),
    ("failed: timeout expired", (
        "Timed out while opening the connection.\n"
        "  - The host/port is likely unreachable (firewall or wrong port)\n"
        "  - Verify connectivity from this machine and check firewall rules"
    )),
    # --- Query-time / runtime failures ---
    ("timeout expired", (
        "The query exceeded the timeout limit.\n"
        "This usually means the database tier is too small for this workload.\n"
        "Try:\n"
        "  - Upgrading to a higher compute tier\n"
        "  - Reducing the scale factor (use 'smoke' profile)\n"
        "  - Increasing the query timeout in config"
    )),
    ("login failed", (
        "Authentication failed — wrong username or password.\n"
        "  - Double-check the username and password (copy-paste to avoid typos)\n"
        "  - Use the server admin login you set when creating the server\n"
        "  - Azure SQL sometimes needs the 'user@servername' format\n"
        "  - For IAM/token auth: ensure your token hasn't expired"
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
