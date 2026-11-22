package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.world.phys.Vec3;

import java.util.EnumMap;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

/** In-memory, server-thread samples. A completed segment freezes before the status is read. */
final class BuildPerformance {
    static final boolean ENABLED = Boolean.getBoolean("mcaibuilder.profile")
            || "1".equals(System.getenv("MC_AI_BUILDER_PROFILE"));
    static volatile int clientFps;

    enum Span {
        APPROVAL, APPROVAL_WAIT, SERVER_TICK, CONSTRUCTION_TICK, INVENTORY, STANCE_SEARCH, PATH_SEARCH,
        TRANSFER_PLAN, TRANSFER, PLACE, BREAK, ACTION_WALL, CHECKPOINT, WORLD_VERIFY, RECOVERY
    }

    private static final class Samples {
        private final long[] histogram = new long[44];
        private long count;
        private long sum;
        private long max;

        void add(long elapsed) {
            count++;
            sum += elapsed;
            max = Math.max(max, elapsed);
            int bucket = elapsed == 0 ? 0 : 64 - Long.numberOfLeadingZeros(elapsed);
            histogram[Math.min(bucket, histogram.length - 1)]++;
        }

        private double percentileUpper(double share) {
            long rank = Math.max(1, (long) Math.ceil(share * count));
            for (int i = 0; i < histogram.length; i++) {
                rank -= histogram[i];
                if (rank <= 0) {
                    return ms(Math.min(max, 1L << i));
                }
            }
            return ms(max);
        }

        JsonObject json() {
            JsonObject result = new JsonObject();
            result.addProperty("samples", count);
            result.addProperty("total_ms", ms(sum));
            result.addProperty("mean_ms", count == 0 ? 0 : ms(sum) / count);
            result.addProperty("max_ms", ms(max));
            result.addProperty("p50_upper_ms", percentileUpper(.50));
            result.addProperty("p95_upper_ms", percentileUpper(.95));
            return result;
        }
    }

    private final Map<Span, Samples> spans = new EnumMap<>(Span.class);
    private final Map<String, Long> wall = new TreeMap<>();
    private final Map<String, Long> outcomes = new TreeMap<>();
    private final Samples fps = new Samples();
    private long started;
    private long firstTick;
    private long lastTick;
    private long lastTime;
    private long lastAction;
    private long ended;
    private String activity = "ready";
    private Vec3 point;
    private double distance;

    static long clock() {
        return ENABLED ? System.nanoTime() : 0;
    }

    private static double ms(long nanos) {
        return nanos / 1_000_000.0;
    }

    void begin(long tick, Vec3 position) {
        if (!ENABLED || started != 0) {
            return;
        }
        started = lastTime = lastAction = clock();
        firstTick = lastTick = tick;
        point = position;
    }

    void span(Span name, long since) {
        if (since != 0) {
            spans.computeIfAbsent(name, ignored -> new Samples()).add(clock() - since);
        }
    }

    void sample(long tick, String next, Vec3 position) {
        if (!ENABLED || started == 0 || ended != 0) {
            return;
        }
        long now = clock();
        wall.merge(activity, now - lastTime, Long::sum);
        lastTime = now;
        lastTick = tick;
        activity = next;
        if (point != null && position != null && !next.startsWith("paused")) {
            distance += point.distanceTo(position);
        }
        point = position;
        if (tick % 20 == 0 && clientFps > 0) {
            // Samples are represented in fixed-point units so the same histogram can be reused.
            fps.add(clientFps * 1_000_000L);
        }
    }

    boolean measuring() {
        return started != 0 && ended == 0;
    }

    double offsetMs() {
        return started == 0 ? 0 : ms(clock() - started);
    }

    void action(String outcome) {
        if (!ENABLED || started == 0 || ended != 0) {
            return;
        }
        span(Span.ACTION_WALL, lastAction);
        lastAction = clock();
        outcomes.merge(outcome, 1L, Long::sum);
    }

    void event(String outcome) {
        if (ENABLED) {
            outcomes.merge(outcome, 1L, Long::sum);
        }
    }

    void stop() {
        if (ENABLED && started != 0 && ended == 0) {
            ended = lastTime;
        }
    }

    JsonObject status() {
        JsonObject result = new JsonObject();
        JsonObject measured = new JsonObject();
        spans.forEach((type, timing) -> measured.add(type.name().toLowerCase(Locale.ROOT), timing.json()));
        result.add("spans", measured);
        JsonObject phases = new JsonObject();
        wall.forEach((state, elapsed) -> phases.addProperty(state, ms(elapsed)));
        result.add("wall_ms_by_activity", phases);
        JsonObject events = new JsonObject();
        outcomes.forEach(events::addProperty);
        result.add("outcomes", events);
        result.addProperty("first_game_tick", firstTick);
        result.addProperty("last_game_tick", lastTick);
        double elapsed = ms((ended == 0 ? lastTime : ended) - started);
        result.addProperty("execution_wall_ms", elapsed);
        result.addProperty("observed_tps", elapsed > 0 ? 1000 * (lastTick - firstTick) / elapsed : 0);
        result.addProperty("observed_fps_mean", fps.count == 0 ? 0 : ms(fps.sum) / fps.count);
        result.addProperty("fps_samples", fps.count);
        result.addProperty("walked_blocks", distance);
        result.addProperty("complete", ended != 0);
        return result;
    }
}
