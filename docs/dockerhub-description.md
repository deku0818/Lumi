# Lumi backend image

Standalone HTTP/WebSocket backend for API integrations. Lumi 0.3 desktop access uses native SSH and a user-managed remote environment. The desktop does not accept container service URLs or tokens.

Bind API ports to loopback and inject LUMI_TOKEN through environment/secret management:

    docker run --rm -p 127.0.0.1:8765:8765 -e LUMI_TOKEN -v "$HOME/.lumi-api:/root/.lumi" ycw0818/lumi-harness

Keep data in a persistent volume and authentication enabled. External API exposure requires appropriate transport security and access controls.

For desktop use, add an SSH host. Existing container data is not automatically merged or taken over, and desktop connections never stop unrelated containers.
