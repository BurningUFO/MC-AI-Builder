package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.ChatScreen;
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
            } else {
                throw new IOException("Unknown isolated chat fixture action");
            }
        } catch (IOException | RuntimeException error) {
            ForgeBridge.LOGGER.warn("Isolated chat fixture input failed", error);
        }
    }

    @SubscribeEvent
    public static void chat(ClientChatReceivedEvent event) {
        Path root = root();
        if (root == null) {
            return;
        }
        String message = event.getMessage().getString();
        if (message.contains("M5_PUBLIC_")) {
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
