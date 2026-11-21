package dev.mcaibuilder.bridge;

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
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Supplier;

final class BridgeHttp implements AutoCloseable {
    private static final int MAX_BODY = 256 * 1024;
    private final HttpServer listener;
    private final ExecutorService io;
    private final MinecraftServer game;
    private final ConstructionService construction;
    private final byte[] authorization;

    BridgeHttp(MinecraftServer game, BuildSite site, ConstructionService construction) throws IOException {
        this.game = game;
        this.construction = construction;
        authorization = ("Bearer " + site.token()).getBytes(StandardCharsets.UTF_8);
        InetAddress loopback = InetAddress.getByAddress(new byte[]{127, 0, 0, 1});
        listener = HttpServer.create(new InetSocketAddress(loopback, site.port()), 8);
        io = Executors.newSingleThreadExecutor(task -> {
            Thread thread = new Thread(task, "MC AI Builder HTTP");
            thread.setDaemon(true);
            return thread;
        });
        listener.setExecutor(io);
        listener.createContext("/", this::handle);
        listener.start();
    }

    private void handle(HttpExchange exchange) throws IOException {
        try (exchange) {
            try {
                String header = exchange.getRequestHeaders().getFirst("Authorization");
                if (header == null || !MessageDigest.isEqual(authorization, header.getBytes(StandardCharsets.UTF_8))) {
                    throw new Wire.Problem(401, "unauthorized", "Bridge token required");
                }
                JsonObject response = dispatch(exchange);
                respond(exchange, 200, response);
            } catch (Wire.Problem problem) {
                respond(exchange, problem.status, error(problem.code, problem.getMessage()));
            } catch (RuntimeException problem) {
                ForgeBridge.LOGGER.error("M1 bridge request failed", problem);
                respond(exchange, 500, error("bridge_error", "See the Minecraft server log"));
            }
        }
    }

    private JsonObject dispatch(HttpExchange exchange) throws IOException {
        String path = exchange.getRequestURI().getRawPath();
        String method = exchange.getRequestMethod();
        return switch (path) {
            case "/status", "/progress" -> {
                requireMethod(method, "GET");
                yield onGameThread(() -> construction.status(path.equals("/progress")));
            }
            case "/build" -> {
                requireMethod(method, "POST");
                JsonObject body = readBody(exchange);
                yield onGameThread(() -> construction.submit(body));
            }
            case "/pause", "/resume", "/cancel" -> {
                requireMethod(method, "POST");
                JsonObject body = readBody(exchange);
                yield onGameThread(() -> construction.control(path.substring(1), BuildPlan.id(body, "job_id")));
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
