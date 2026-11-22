package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.minecraft.server.MinecraftServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.Set;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Supplier;

final class BridgeHttp implements AutoCloseable {
    private static final int MAX_BODY = 256 * 1024;
    private final HttpServer listener;
    private final ExecutorService io;
    private final MinecraftServer game;
    private static final Set<String> COPY_READS = Set.of("/health", "/status", "/progress", "/change", "/versions");
    private final Supplier<ConstructionService> construction;
    private final Supplier<ArchitectService> architect;
    private final CopySites copy;
    private final NpcObservations observations;
    private long runtimePoll;
    private long runtimePolls;
    private final byte[] authorization;
    private final byte[] architectAuthorization;

    BridgeHttp(MinecraftServer game, int port, String token, String architectToken,
               Supplier<ConstructionService> construction, Supplier<ArchitectService> architect,
               CopySites copy, NpcObservations observations) throws IOException {
        this.game = game;
        this.construction = construction;
        this.architect = architect;
        this.copy = copy;
        this.observations = observations;
        architectAuthorization = architectToken == null ? null : bearer(architectToken);
        authorization = bearer(token);
        InetAddress loopback = InetAddress.getByAddress(new byte[]{127, 0, 0, 1});
        listener = HttpServer.create(new InetSocketAddress(loopback, port), 8);
        io = Executors.newSingleThreadExecutor(task -> {
            Thread thread = new Thread(task, "MC AI Builder HTTP");
            thread.setDaemon(true);
            return thread;
        });
        listener.setExecutor(io);
        listener.createContext("/", this::handle);
        listener.start();
    }

    private static byte[] bearer(String token) {
        return ("Bearer " + token).getBytes(StandardCharsets.UTF_8);
    }

    private void handle(HttpExchange exchange) throws IOException {
        try (exchange) {
            try {
                String header = exchange.getRequestHeaders().getFirst("Authorization");
                String path = exchange.getRequestURI().getRawPath();
                byte[] expected = path.startsWith("/architect/") ? architectAuthorization : authorization;
                if (expected == null || header == null || !MessageDigest.isEqual(expected, header.getBytes(StandardCharsets.UTF_8))) {
                    throw new Wire.Problem(401, "unauthorized", "Bridge token required");
                }
                JsonObject response = dispatch(exchange);
                respond(exchange, 200, response);
            } catch (Wire.Problem problem) {
                respond(exchange, problem.status, error(problem.code, problem.getMessage()));
            } catch (RuntimeException problem) {
                ForgeBridge.LOGGER.error("Construction bridge request failed", problem);
                respond(exchange, 500, error("bridge_error", "See the Minecraft server log"));
            }
        }
    }

    private JsonObject architectHealth() {
        JsonObject health = new JsonObject();
        ArchitectService service = architect.get();
        health.addProperty("architect_enabled", architectAuthorization != null);
        health.addProperty("architect_runtime_online",
                runtimePoll != 0 && System.nanoTime() - runtimePoll < Duration.ofSeconds(6).toNanos());
        health.addProperty("architect_runtime_polls", runtimePolls);
        health.addProperty("architect_player_online", copy != null ? copy.playerOnline() : service != null && service.playerOnline());
        if (copy != null) {
            health.add("site_authorization", copy.status());
        }
        return health;
    }

    private JsonObject inbox() {
        runtimePoll = System.nanoTime();
        runtimePolls++;
        ArchitectService service = architect.get();
        if (service == null) {
            JsonObject empty = new JsonObject();
            empty.add("requests", new JsonArray());
            return empty;
        }
        return service.inbox();
    }

    private ArchitectService architect() {
        ArchitectService service = architect.get();
        if (service == null) {
            throw Wire.conflict("site_not_approved", "请先批准施工区。");
        }
        return service;
    }

    private ConstructionService construction() {
        ConstructionService service = construction.get();
        if (service == null) {
            throw Wire.conflict("site_not_approved", "请先批准施工区。");
        }
        return service;
    }

    private JsonObject status(boolean detailed) {
        ConstructionService service = construction.get();
        JsonObject result = service == null ? new JsonObject() : service.status(detailed);
        if (copy != null) {
            result.add("site_authorization", copy.status());
        }
        return result;
    }

    private JsonObject dispatch(HttpExchange exchange) throws IOException {
        String path = exchange.getRequestURI().getRawPath();
        String method = exchange.getRequestMethod();
        if (copy != null && !path.startsWith("/architect/") && !COPY_READS.contains(path)) {
            throw new Wire.Problem(403, "copy_player_confirmation_required",
                    "World copies require approval through the saved local player in Minecraft");
        }
        return switch (path) {
            case "/architect/observation" -> {
                requireMethod(method, "GET");
                yield onGameThread(() -> observations == null
                        ? NpcObservations.state("unavailable") : observations.latest());
            }
            case "/architect/observe_world" -> {
                requireMethod(method, "GET");
                yield onGameThread(() -> {
                    if (observations == null) {
                        throw Wire.conflict("observation_unavailable", "此世界没有本地 NPC 相机。");
                    }
                    return observations.observeWorld(construction.get(), architect.get());
                });
            }
            case "/architect/inbox" -> {
                requireMethod(method, "GET");
                yield onGameThread(this::inbox);
            }
            case "/architect/stage", "/architect/answer", "/architect/pause" -> {
                requireMethod(method, "POST");
                JsonObject body = readBody(exchange);
                yield onGameThread(() -> switch (path) {
                    case "/architect/stage" -> architect().stage(body);
                    case "/architect/answer" -> architect().answer(body);
                    default -> architect().pauseRequest(body);
                });
            }
            case "/health" -> {
                requireMethod(method, "GET");
                yield onGameThread(this::architectHealth);
            }
            case "/status", "/progress" -> {
                requireMethod(method, "GET");
                yield onGameThread(() -> status(path.equals("/progress")));
            }
            case "/change", "/versions" -> {
                requireMethod(method, "GET");
                yield onGameThread(() -> path.equals("/change") ? construction().change() : construction().versions());
            }
            case "/candidate" -> {
                requireMethod(method, "POST");
                JsonObject body = readBody(exchange);
                yield onGameThread(() -> construction().propose(BuildPlan.id(body, "job_id"),
                        Wire.object(body.get("plan"), "plan")));
            }
            case "/approve", "/reject" -> {
                requireMethod(method, "POST");
                JsonObject body = readBody(exchange);
                yield onGameThread(() -> {
                    var id = BuildPlan.id(body, "change_id");
                    if (path.equals("/reject")) {
                        return construction().reject(id);
                    }
                    String hash = Wire.string(body.get("review_sha256"), "review_sha256");
                    if (!hash.matches("[0-9a-f]{64}")) {
                        throw Wire.invalid("The reviewed SHA-256 is required");
                    }
                    return construction().approve(id, hash);
                });
            }
            case "/build" -> {
                requireMethod(method, "POST");
                JsonObject body = readBody(exchange);
                yield onGameThread(() -> construction().submit(body));
            }
            case "/pause", "/resume", "/cancel" -> {
                requireMethod(method, "POST");
                JsonObject body = readBody(exchange);
                yield onGameThread(() -> construction().control(path.substring(1), BuildPlan.id(body, "job_id")));
            }
            default -> throw new Wire.Problem(404, "unknown_endpoint", "Unknown bridge endpoint");
        };
    }

    private static void requireMethod(String actual, String expected) {
        if (!actual.equals(expected)) {
            throw new Wire.Problem(405, "invalid_method", "Use " + expected);
        }
    }

    private static JsonObject readBody(HttpExchange exchange) throws IOException {
        String contentType = exchange.getRequestHeaders().getFirst("Content-Type");
        if (contentType == null || !contentType.split(";", 2)[0].strip().equalsIgnoreCase("application/json")) {
            throw new Wire.Problem(415, "invalid_content_type", "Expected application/json");
        }
        byte[] body = exchange.getRequestBody().readNBytes(MAX_BODY + 1);
        if (body.length > MAX_BODY) {
            throw new Wire.Problem(413, "plan_too_large", "JSON body exceeds 256 KiB");
        }
        try {
            return Wire.object(JsonParser.parseString(new String(body, StandardCharsets.UTF_8)), "body");
        } catch (JsonParseException error) {
            throw Wire.invalid("Malformed JSON");
        }
    }

    private JsonObject onGameThread(Supplier<JsonObject> action) {
        try {
            return game.submit(action).get();
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new Wire.Problem(503, "server_stopping", "The Minecraft server is stopping");
        } catch (ExecutionException error) {
            if (error.getCause() instanceof Wire.Problem problem) {
                throw problem;
            }
            throw new IllegalStateException(error.getCause());
        }
    }

    private static JsonObject error(String code, String message) {
        JsonObject error = new JsonObject();
        error.addProperty("error", code);
        error.addProperty("message", message);
        return error;
    }

    private static void respond(HttpExchange exchange, int status, JsonObject body) throws IOException {
        byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.sendResponseHeaders(status, data.length);
        exchange.getResponseBody().write(data);
    }

    @Override
    public void close() {
        listener.stop(0);
        io.shutdownNow();
    }
}
