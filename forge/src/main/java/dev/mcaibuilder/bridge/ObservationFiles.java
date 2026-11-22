package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;

final class ObservationFiles {
    private static final int KEEP = 24;
    private static final int KEEP_ORIGINALS = 2;
    private static final String ID = "[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}";

    private ObservationFiles() {}

    static JsonObject save(Path directory, JsonObject data, NpcObservations.Frame frame) throws IOException {
        // Check each cache component before creation, including an existing Windows junction.
        ensureDirectory(directory.getParent().getParent());
        ensureDirectory(directory.getParent());
        ensureDirectory(directory);
        String id = data.get("observation_id").getAsString();
        String image = id + ".png";
        writeImage(directory.resolve(image), frame.png());
        try {
            if (frame.original() != null) {
                writeImage(directory.resolve(id + ".original.png"), frame.original());
            }
            data.addProperty("image_path", image);
            data.addProperty("image_sha256", sha256(frame.png()));
            JobJournal.write(directory.resolve(id + ".json"), data);
        } catch (IOException error) {
            Files.deleteIfExists(directory.resolve(image));
            Files.deleteIfExists(directory.resolve(id + ".original.png"));
            data.remove("image_path");
            throw error;
        }
        try {
            prune(directory, ".json", KEEP);
            prune(directory, ".original.png", KEEP_ORIGINALS);
        } catch (IOException error) {
            ForgeBridge.LOGGER.warn("Could not prune the local observation cache", error);
        }
        return data;
    }

    private static void ensureDirectory(Path path) throws IOException {
        if (Files.notExists(path, LinkOption.NOFOLLOW_LINKS)) {
            Files.createDirectories(path);
        }
        if (!Files.isDirectory(path, LinkOption.NOFOLLOW_LINKS) || !path.equals(path.toRealPath())) {
            throw new IOException("Observation cache must be a local directory");
        }
    }

    private static void writeImage(Path path, byte[] image) throws IOException {
        Path temp = Files.createTempFile(path.getParent(), "capture-", ".part");
        try {
            Files.write(temp, image);
            Files.move(temp, path, StandardCopyOption.ATOMIC_MOVE);
        } finally {
            Files.deleteIfExists(temp);
        }
    }

    private static void prune(Path directory, String suffix, int keep) throws IOException {
        List<Path> files;
        try (var stream = Files.list(directory)) {
            files = stream.filter(path -> path.getFileName().toString().matches(ID + java.util.regex.Pattern.quote(suffix)))
                    .filter(path -> Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
                    .sorted(Comparator.comparingLong((Path path) -> path.toFile().lastModified()).reversed())
                    .toList();
        }
        for (Path file : files.subList(Math.min(keep, files.size()), files.size())) {
            if (suffix.equals(".json")) {
                String id = file.getFileName().toString().substring(0, 36);
                Files.deleteIfExists(directory.resolve(id + ".png"));
            }
            Files.deleteIfExists(file);
        }
    }

    private static String sha256(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }
}
