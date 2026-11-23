package ai.yuki.mcp;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;

class McpManagementClientTest {
    private HttpServer server;
    private final AtomicReference<String> requestFailure = new AtomicReference<>();

    private McpManagementClient client(String response, int status, AtomicBoolean called)
            throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v1/integrations/mcp/provider/account/tools", exchange -> {
            called.set(true);
            if (!"GET".equals(exchange.getRequestMethod())) {
                requestFailure.set("method");
            }
            if (!"Bearer ephemeral-session".equals(
                    exchange.getRequestHeaders().getFirst("Authorization"))) {
                requestFailure.set("authorization");
            }
            if (!"application/json".equals(
                    exchange.getRequestHeaders().getFirst("Accept"))) {
                requestFailure.set("accept");
            }
            byte[] body = response.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        server.start();
        URI base = URI.create("http://127.0.0.1:" + server.getAddress().getPort());
        return new McpManagementClient(base, "ephemeral-session", Set.of("127.0.0.1"),
                Duration.ofSeconds(2), Duration.ofSeconds(3));
    }

    @AfterEach
    void stopServer() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void returnsInertTypedCatalogWithoutExecutionSurface() throws Exception {
        String body = """
                {"provider_id":"provider","account_id":"account","server_info":{"name":"test"},
                 "protocol_version":"2025-03-26","tools_trust":"untrusted_remote_server",
                 "execution_enabled":false,
                 "tools":[{"name":"status.read","description":"Read status",
                 "input_schema":{"type":"object"},"metadata_trust":"untrusted_remote_server",
                 "read_only":false,"execution_enabled":false}]}
                """;
        AtomicBoolean called = new AtomicBoolean();
        try (McpManagementClient client = client(body, 200, called)) {
            var catalog = client.discoverTools("provider", "account");
            assertTrue(called.get());
            assertNull(requestFailure.get());
            assertEquals(1, catalog.tools().size());
            assertEquals("status.read", catalog.tools().get(0).name());
            assertFalse(catalog.executionEnabled());
            assertFalse(catalog.tools().get(0).executionEnabled());
            assertFalse(catalog.tools().get(0).readOnly());
        }
    }

    @Test
    void rejectsRemoteMetadataThatClaimsExecutionEnabled() throws Exception {
        String body = """
                {"provider_id":"provider","account_id":"account","server_info":{},
                 "protocol_version":"v1","tools_trust":"untrusted",
                 "execution_enabled":true,"tools":[]}
                """;
        AtomicBoolean called = new AtomicBoolean();
        try (McpManagementClient client = client(body, 200, called)) {
            assertThrows(IOException.class, () -> client.discoverTools("provider", "account"));
        }
    }

    @Test
    void rejectsHttpErrorsWithoutExposingResponseBody() throws Exception {
        AtomicBoolean called = new AtomicBoolean();
        try (McpManagementClient client = client("{\"secret\":\"do-not-echo\"}", 401, called)) {
            IOException error = assertThrows(IOException.class,
                    () -> client.discoverTools("provider", "account"));
            assertFalse(error.getMessage().contains("do-not-echo"));
        }
    }

    @Test
    void rejectsInvalidIdentityAndUnsafeBaseUri() throws Exception {
        AtomicBoolean called = new AtomicBoolean();
        try (McpManagementClient client = client("{}", 200, called)) {
            assertThrows(IllegalArgumentException.class,
                    () -> client.discoverTools("../provider", "account"));
        }
        assertThrows(IllegalArgumentException.class, () -> new McpManagementClient(
                URI.create("http://example.com"), "token", Set.of("example.com"),
                Duration.ofSeconds(1), Duration.ofSeconds(1)));
        assertThrows(IllegalArgumentException.class, () -> new McpManagementClient(
                URI.create("https://example.com"), "token", Set.of("other.example"),
                Duration.ofSeconds(1), Duration.ofSeconds(1)));
        assertThrows(IllegalArgumentException.class, () -> new McpManagementClient(
                URI.create("https://example.com:8443"), "token", Set.of("example.com"),
                Duration.ofSeconds(1), Duration.ofSeconds(1)));
    }


    @Test
    void rejectsOversizedResponseBody() throws Exception {
        String body = "x".repeat(1_048_577);
        AtomicBoolean called = new AtomicBoolean();
        try (McpManagementClient client = client(body, 200, called)) {
            assertThrows(IOException.class, () -> client.discoverTools("provider", "account"));
        }
    }

    @Test
    void rejectsMismatchedConnectionIdentity() throws Exception {
        String body = """
                {"provider_id":"other","account_id":"account","server_info":{},
                 "protocol_version":"v1","tools_trust":"untrusted",
                 "execution_enabled":false,"tools":[]}
                """;
        AtomicBoolean called = new AtomicBoolean();
        try (McpManagementClient client = client(body, 200, called)) {
            assertThrows(IOException.class, () -> client.discoverTools("provider", "account"));
        }
    }
}
