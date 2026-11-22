package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.ChatScreen;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.ClickEvent;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.client.event.ClientChatReceivedEvent;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

/** Submit text through vanilla ChatScreen in an isolated acceptance client. */
@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT)
final class LocalChatFixture {
    private static Path root() {
        Minecraft client = Minecraft.getInstance();
        Path root = client.gameDirectory.toPath();
        boolean fixture = CopyProfile.fixture(root) || (Boolean.getBoolean("mcaibuilder.localTest")
                && Boolean.getBoolean("mcaibuilder.m5Smoke") && Files.isRegularFile(root.resolve(".mcaibuilder-test")));
        return fixture && client.getSingleplayerServer() != null && client.player != null
                && client.getConnection() != null ? root : null;
    }

    @SubscribeEvent
    public static void tick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END) {
            return;
        }
        Path root = root();
        if (root == null) {
            return;
        }
        Path file = root.resolve(".mcaibuilder-chat-fixture");
        if (!Files.isRegularFile(file)) {
            return;
        }
        try {
            JsonObject request = Wire.object(JsonParser.parseString(Files.readString(file)), "chat fixture");
            Files.delete(file);
            String action = Wire.string(request.get("action"), "action");
            Minecraft client = Minecraft.getInstance();
            if (action.equals("chat")) {
                String message = Wire.string(request.get("message"), "message");
                if (message.length() > 256 || message.chars().anyMatch(Character::isISOControl)) {
                    throw new IOException("Invalid fixture chat");
                }
                ChatScreen chat = new ChatScreen("");
                client.setScreen(chat);
                if (chat.handleChatInput(message, true)) {
                    client.setScreen(null);
                }
            } else if (action.equals("probe_preview")) {
                var pos = Wire.position(request.get("position"), "preview probe");
                var state = client.level.getBlockState(pos);
                var reply = new JsonObject();
                reply.add("request", request.get("request"));
                reply.addProperty("ghost_cells", GhostPreviewClient.size());
                reply.add("block", Wire.state(state));
                reply.add("projection", Wire.state(GhostPreviewClient.stateAt(pos)));
                reply.addProperty("collides", !state.getCollisionShape(client.level, pos).isEmpty());
                Files.writeString(root.resolve(".mcaibuilder-preview-result"), reply.toString());
            } else {
                throw new IOException("Unknown isolated chat fixture action");
            }
        } catch (IOException | RuntimeException error) {
            ForgeBridge.LOGGER.warn("Isolated chat fixture input failed", error);
        }
    }

    static void recordProjectLinks(Component message) {
        Path root = root();
        if (root == null) {
            return;
        }
        JsonObject data = new JsonObject();
        data.addProperty("message", message.getString());
        var files = new com.google.gson.JsonArray();
        for (Component child : message.getSiblings()) {
            var event = child.getStyle().getClickEvent();
            if (event != null && event.getAction() == ClickEvent.Action.OPEN_FILE) {
                files.add(event.getValue());
            }
        }
        data.add("files", files);
        try {
            Files.writeString(root.resolve("project-links.jsonl"), data + "\n", StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException error) {
            ForgeBridge.LOGGER.warn("Could not record isolated Blender links", error);
        }
    }

    @SubscribeEvent
    public static void chat(ClientChatReceivedEvent event) {
        Path root = root();
        if (root == null) {
            return;
        }
        String message = event.getMessage().getString();
        if (message.contains("M5_PUBLIC_") || message.startsWith("[Builder] ")) {
            JsonObject data = new JsonObject();
            data.addProperty("message", message);
            try {
                Files.writeString(root.resolve("public-chat.jsonl"), data + "\n",
                        StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            } catch (IOException error) {
                ForgeBridge.LOGGER.warn("Could not record isolated ordinary chat", error);
            }
        }
    }
}
