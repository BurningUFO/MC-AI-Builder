package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.storage.LevelResource;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;

final class JobJournal {
    private final Path path;
    private final JsonObject location;

    JobJournal(ServerLevel level, BuildSite site) {
        path = level.getServer().getWorldPath(LevelResource.ROOT).resolve("data").resolve("mcaibuilder-journal.json");
        location = new JsonObject();
        location.addProperty("world", site.world());
        location.addProperty("dimension", level.dimension().location().toString());
        location.add("area", site.area().json());
        location.add("spawn", Wire.position(site.spawn()));
    }

    JsonObject read() throws IOException {
        if (Files.notExists(path)) {
            return null;
        }
        JsonObject data = Wire.object(JsonParser.parseString(Files.readString(path)), "journal");
        if (Wire.integer(data.get("schema"), "journal.schema") != 1 || !location.equals(data.get("site"))) {
            throw new IOException("Construction journal belongs to another site or schema");
        }
        return data;
    }

    void write(JsonObject data) throws IOException {
        data.addProperty("schema", 1);
        data.add("site", location);
        Files.createDirectories(path.getParent());
        Path temp = path.resolveSibling("mcaibuilder-journal.tmp");
        ByteBuffer bytes = StandardCharsets.UTF_8.encode(data.toString());
        try (FileChannel channel = FileChannel.open(temp, StandardOpenOption.WRITE,
                StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING)) {
            while (bytes.hasRemaining()) {
                channel.write(bytes);
            }
            channel.force(true);
        }
        Files.move(temp, path, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    }
}
