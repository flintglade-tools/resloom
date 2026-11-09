# Security

Report vulnerabilities privately through [GitHub private vulnerability reporting](https://github.com/flintglade-tools/resloom/security/advisories/new), or at [support@flintglade.com](mailto:support@flintglade.com).

Fixes go to the `main` branch. There are no maintained older branches.

Resloom has no accounts or sign-in. The bundled Streamlit configuration listens on `127.0.0.1` with Streamlit's cross-site request protection on, and `docker-compose.yml` publishes the container's port on `127.0.0.1` only. The app stops before reading any data unless the request's Host header is `127.0.0.1`, `localhost` or `[::1]`, which blocks DNS-rebinding pages. The container runs as an unprivileged user. Anyone who can open the app as one of those hosts has full read and write access to the database. The optional report assistant sends the report's source records to an Ollama server at `localhost:11434` only when it is ticked for a report.
