package ai.yuki.mcp;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.StreamReadConstraints;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Read-only adapter for Yuki's authenticated MCP management API.
 * Python remains authoritative for OAuth, sessions, owner binding, scopes,
 * tool policy, approval and audit. This class exposes no execution method.
 */
public final class McpManagementClient implements AutoCloseable {
    private static final int MAX_RESPONSE_BYTES = 1_048_576;
    private static final Pattern IDENTIFIER =
            Pattern.compile("[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}");
    private static final ObjectMapper JSON = new ObjectMapper(JsonFactory.builder()
            .streamReadConstraints(StreamReadConstraints.builder()
                    .maxNestingDepth(64)
                    .maxStringLength(8192)
                    .maxNumberLength(128)
                    .build())
            .build());

    private final URI baseUri;
    private final String sessionToken;
    private final HttpClient http;
    private final Duration requestTimeout;

    public record ToolMetadata(String name, String description, JsonNode inputSchema,
                               String metadataTrust, boolean readOnly,
                               boolean executionEnabled) { }

    public record ToolCatalog(String providerId, String accountId, JsonNode serverInfo,
                              String protocolVersion, String toolsTrust,
                              boolean executionEnabled, List<ToolMetadata> tools) {
        public ToolCatalog {
            tools = List.copyOf(tools);
        }
    }

    public McpManagementClient(URI baseUri, String sessionToken, Set<String> allowedHosts,
                               Duration connectTimeout, Duration requestTimeout) {
        this.baseUri = validateBaseUri(baseUri, allowedHosts);
        this.sessionToken = validateToken(sessionToken);
        this.requestTimeout = validateTimeout(requestTimeout, "request timeout");
        Duration connect = validateTimeout(connectTimeout, "connect timeout");
        this.http = HttpClient.newBuilder()
                .connectTimeout(connect)
                .followRedirects(HttpClient.Redirect.NEVER)
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    private static URI validateBaseUri(URI uri, Set<String> allowedHosts) {
        Objects.requireNonNull(uri, "base URI is required");
        Objects.requireNonNull(allowedHosts, "host allow-list is required");
        String host = uri.getHost();
        if (host == null || uri.getUserInfo() != null || uri.getQuery() != null
                || uri.getFragment() != null) {
            throw new IllegalArgumentException("invalid Yuki API base URI");
        }
        boolean loopback = host.equalsIgnoreCase("localhost")
                || host.equals("127.0.0.1") || host.equals("::1");
        boolean secureScheme = "https".equalsIgnoreCase(uri.getScheme());
        boolean loopbackHttp = "http".equalsIgnoreCase(uri.getScheme()) && loopback;
        if (!secureScheme && !loopbackHttp) {
            throw new IllegalArgumentException("HTTPS required except for loopback development");
        }
        if ((secureScheme && uri.getPort() != -1 && uri.getPort() != 443)
                || (loopbackHttp && uri.getPort() != -1 && uri.getPort() != 80
                    && uri.getPort() != 8080 && uri.getPort() != 8000)) {
            throw new IllegalArgumentException("Yuki API port is not allow-listed");
        }
        String normalizedHost = host.toLowerCase(Locale.ROOT);
        boolean allowed = allowedHosts.stream().filter(Objects::nonNull)
                .map(value -> value.toLowerCase(Locale.ROOT))
                .anyMatch(normalizedHost::equals);
        if (!allowed) {
            throw new IllegalArgumentException("Yuki API host is not allow-listed");
        }
        String path = uri.getPath() == null ? "" : uri.getPath();
        if (path.contains("..") || path.contains("%")) {
            throw new IllegalArgumentException("invalid Yuki API base path");
        }
        return uri;
    }

    private static String validateToken(String token) {
        if (token == null || token.isBlank() || token.length() > 4096
                || token.chars().anyMatch(Character::isWhitespace)) {
            throw new IllegalArgumentException("invalid session token");
        }
        return token;
    }

    private static Duration validateTimeout(Duration timeout, String label) {
        if (timeout == null || timeout.isZero() || timeout.isNegative()
                || timeout.compareTo(Duration.ofSeconds(30)) > 0) {
            throw new IllegalArgumentException(label + " must be in (0, 30s]");
        }
        return timeout;
    }

    /** Fetch inert metadata for an existing owner-bound MCP connection. */
    public ToolCatalog discoverTools(String providerId, String accountId)
            throws IOException, InterruptedException {
        String provider = validateIdentifier(providerId, "provider ID");
        String account = validateIdentifier(accountId, "account ID");
        HttpRequest request = HttpRequest.newBuilder(endpoint(provider, account))
                .timeout(requestTimeout)
                .header("Authorization", "Bearer " + sessionToken)
                .header("Accept", "application/json")
                .GET()
                .build();
        HttpResponse<InputStream> response = http.send(
                request, HttpResponse.BodyHandlers.ofInputStream());
        try (InputStream body = response.body()) {
            if (response.statusCode() != 200) {
                body.readNBytes(4096);
                throw new IOException("Yuki MCP metadata request failed with HTTP "
                        + response.statusCode());
            }
            JsonNode root = JSON.readTree(readBounded(body));
            if (root == null || !root.isObject()) {
                throw new IOException("invalid Yuki MCP metadata response");
            }
            if (!provider.equals(requiredText(root, "provider_id"))
                    || !account.equals(requiredText(root, "account_id"))) {
                throw new IOException("Yuki MCP metadata identity mismatch");
            }
            if (requiredBoolean(root, "execution_enabled")) {
                throw new IOException("Yuki MCP response unexpectedly enables execution");
            }
            JsonNode rawTools = root.path("tools");
            if (!rawTools.isArray() || rawTools.size() > 256) {
                throw new IOException("invalid Yuki MCP tool catalog");
            }
            List<ToolMetadata> tools = new ArrayList<>(rawTools.size());
            for (JsonNode raw : rawTools) {
                String name = requiredText(raw, "name");
                if (!IDENTIFIER.matcher(name).matches()
                        || requiredBoolean(raw, "read_only")
                        || requiredBoolean(raw, "execution_enabled")) {
                    throw new IOException("remote MCP metadata violates inert catalog contract");
                }
                JsonNode schema = raw.path("input_schema");
                if (!schema.isObject() && !schema.isBoolean()) {
                    throw new IOException("invalid MCP input schema metadata");
                }
                tools.add(new ToolMetadata(name, optionalText(raw, "description"),
                        schema.deepCopy(), requiredText(raw, "metadata_trust"), false, false));
            }
            return new ToolCatalog(provider, account, root.path("server_info").deepCopy(),
                    optionalText(root, "protocol_version"), requiredText(root, "tools_trust"),
                    false, tools);
        }
    }

    private URI endpoint(String provider, String account) {
        String prefix = baseUri.toString().replaceAll("/+$", "");
        return URI.create(prefix + "/v1/integrations/mcp/" + provider + "/" + account + "/tools");
    }

    private static String validateIdentifier(String value, String label) {
        if (value == null || !IDENTIFIER.matcher(value).matches()) {
            throw new IllegalArgumentException("invalid " + label);
        }
        return value;
    }

    private static String requiredText(JsonNode node, String field) throws IOException {
        JsonNode value = node.path(field);
        if (!value.isTextual() || value.textValue().isBlank()
                || value.textValue().length() > 4096) {
            throw new IOException("invalid MCP response field: " + field);
        }
        return value.textValue();
    }

    private static String optionalText(JsonNode node, String field) throws IOException {
        JsonNode value = node.path(field);
        if (value.isMissingNode() || value.isNull()) {
            return "";
        }
        if (!value.isTextual() || value.textValue().length() > 8192) {
            throw new IOException("invalid MCP response field: " + field);
        }
        return value.textValue();
    }

    private static boolean requiredBoolean(JsonNode node, String field) throws IOException {
        JsonNode value = node.path(field);
        if (!value.isBoolean()) {
            throw new IOException("invalid MCP response field: " + field);
        }
        return value.booleanValue();
    }

    private static byte[] readBounded(InputStream input) throws IOException {
        byte[] bytes = input.readNBytes(MAX_RESPONSE_BYTES + 1);
        if (bytes.length > MAX_RESPONSE_BYTES) {
            throw new IOException("Yuki MCP response exceeds 1 MiB");
        }
        return bytes;
    }

    @Override
    public void close() {
        // Java 17 HttpClient has no close API; no token is persisted by this adapter.
    }
}
