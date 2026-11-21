package dev.mcaibuilder.bridge;

import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.core.registries.Registries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.Difficulty;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.LevelSettings;
import net.minecraft.world.level.WorldDataConfiguration;
import net.minecraft.world.level.levelgen.WorldOptions;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.event.entity.player.PlayerEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/** Opens the isolated fixture only when launched by tools/m1_client.py. */
@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT)
final class LocalTestWorld {
    private static final String WORLD_NAME = "M1 Construction Lab";
    private static final String WORLD_FOLDER = "m1-construction";
    private static boolean opened;

    private static boolean enabled() {
        return Boolean.getBoolean("mcaibuilder.localTest")
                && Files.isRegularFile(Minecraft.getInstance().gameDirectory.toPath().resolve(".mcaibuilder-test"));
    }

    @SubscribeEvent
    public static void onClientTick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END || !enabled()) {
            return;
        }
        Minecraft client = Minecraft.getInstance();
        Path root = client.gameDirectory.toPath();
        if (!opened && client.screen instanceof TitleScreen title) {
            opened = true;
            if (Files.isRegularFile(root.resolve("saves").resolve(WORLD_FOLDER).resolve("level.dat"))) {
                client.createWorldOpenFlows().loadLevel(title, WORLD_FOLDER);
            } else {
                createWorld(client);
            }
        }
        if (client.level != null && Boolean.getBoolean("mcaibuilder.smoke")) {
            try {
                if (Files.deleteIfExists(root.resolve(".mcaibuilder-capture"))) {
                    Screenshot.grab(client.gameDirectory, "m1-wall-built.png", client.getMainRenderTarget(),
                            message -> ForgeBridge.LOGGER.info("M1 screenshot: {}", message.getString()));
                }
                if (Files.deleteIfExists(root.resolve(".mcaibuilder-stop"))) {
                    client.stop();
                }
            } catch (IOException error) {
                ForgeBridge.LOGGER.warn("M1 fixture signal failed", error);
            }
        }
    }

    private static void createWorld(Minecraft client) {
        GameRules rules = new GameRules();
        rules.getRule(GameRules.RULE_RANDOMTICKING).set(0, null);
        rules.getRule(GameRules.RULE_DOMOBSPAWNING).set(false, null);
        rules.getRule(GameRules.RULE_DAYLIGHT).set(false, null);
        rules.getRule(GameRules.RULE_SPAWN_RADIUS).set(0, null);
        LevelSettings settings = new LevelSettings(WORLD_NAME, GameType.CREATIVE, false,
                Difficulty.PEACEFUL, true, rules, WorldDataConfiguration.DEFAULT);
        client.createWorldOpenFlows().createFreshLevel(WORLD_FOLDER, settings, new WorldOptions(1090L, false, false),
                access -> access.registryOrThrow(Registries.WORLD_PRESET).getOrThrow(WorldPresets.FLAT).createWorldDimensions());
    }

    @SubscribeEvent
    public static void onJoin(PlayerEvent.PlayerLoggedInEvent event) {
        if (enabled() && event.getEntity() instanceof ServerPlayer player
                && player.getServer() != null && player.getServer().getWorldData().getLevelName().equals(WORLD_NAME)) {
            player.teleportTo(player.serverLevel(), 9.5, -60, 14.5, 180, 0);
        }
    }
}
