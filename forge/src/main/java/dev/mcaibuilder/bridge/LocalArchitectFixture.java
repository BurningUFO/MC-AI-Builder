package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import com.mojang.authlib.GameProfile;
import net.minecraft.client.Minecraft;
import net.minecraft.server.level.ServerPlayer;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.client.event.ClientChatReceivedEvent;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.UUID;

/** Drives only the architect command of a logged-in client in the isolated acceptance fixture. */
@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT)
final class LocalArchitectFixture {
    private static final String PREFIX = "[Architect] ";

    private static Path fixtureRoot() {
        Path root = Minecraft.getInstance().gameDirectory.toPath();
        return Boolean.getBoolean("mcaibuilder.m3Smoke") && Boolean.getBoolean("mcaibuilder.localTest")
                && Files.isRegularFile(root.resolve(".mcaibuilder-test")) ? root : null;
    }

    @SubscribeEvent
    public static void clientTick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END) {
            return;
        }
        Path root = fixtureRoot();
        Minecraft client = Minecraft.getInstance();
        if (root == null || client.getConnection() == null || client.player == null
                || client.getSingleplayerServer() == null) {
            return;
        }
        try {
            Path commandFile = root.resolve(".mcaibuilder-architect-command");
            if (Files.isRegularFile(commandFile)) {
                String command = Files.readString(commandFile).strip();
                Files.delete(commandFile);
                if (!command.startsWith("architect ") || command.length() > 250
                        || command.chars().anyMatch(Character::isISOControl)) {
                    throw new IOException("Expected one Architect fixture command");
                }
                client.getConnection().sendCommand(command);
            }
            Path guestFile = root.resolve(".mcaibuilder-guest-approve");
            if (Files.isRegularFile(guestFile)) {
                String code = UUID.fromString(Files.readString(guestFile).strip()).toString();
                Files.delete(guestFile);
                var server = client.getSingleplayerServer();
                UUID observedPlayer = client.player.getUUID();
                server.execute(() -> {
                    ServerPlayer listener = server.getPlayerList().getPlayer(observedPlayer);
                    if (listener != null) {
                        ServerPlayer guest = new ServerPlayer(server, server.overworld(),
                                new GameProfile(UUID.randomUUID(), "UnlistedTester"));
                        guest.setPos(listener.getX(), listener.getY(), listener.getZ());
                        server.getCommands().performPrefixedCommand(
                                listener.createCommandSourceStack().withEntity(guest), "architect approve " + code);
                    }
                });
            }
        } catch (IOException | IllegalArgumentException error) {
            ForgeBridge.LOGGER.warn("Architect fixture command failed", error);
        }
    }

    @SubscribeEvent
    public static void clientChat(ClientChatReceivedEvent event) {
        Path root = fixtureRoot();
        String message = event.getMessage().getString();
        if (root == null || !message.startsWith(PREFIX)) {
            return;
        }
        JsonObject row = new JsonObject();
        row.addProperty("message", message);
        try {
            Files.writeString(root.resolve("architect-chat.jsonl"), row + "\n",
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException error) {
            ForgeBridge.LOGGER.warn("Could not record Architect fixture response", error);
        }
    }
}
