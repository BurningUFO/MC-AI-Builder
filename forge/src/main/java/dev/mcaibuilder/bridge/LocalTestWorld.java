package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.screens.GenericDirtMessageScreen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.Difficulty;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.LevelSettings;
import net.minecraft.world.level.WorldDataConfiguration;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.level.levelgen.WorldOptions;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.event.entity.player.PlayerEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;

import java.util.UUID;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/** Opens the isolated fixture only when launched by tools/m1_client.py. */
@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT)
final class LocalTestWorld {
    private static final String WORLD_NAME = "M1 Construction Lab";
    private static final String WORLD_FOLDER = "m1-construction";
    private static boolean opened;
    private static boolean leavingWorld;

    private static boolean enabled() {
        return Boolean.getBoolean("mcaibuilder.localTest")
                && Files.isRegularFile(Minecraft.getInstance().gameDirectory.toPath().resolve(".mcaibuilder-test"));
    }

    @SubscribeEvent
    public static void onClientTick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END) {
            return;
        }
        Minecraft client = Minecraft.getInstance();
        Path root = client.gameDirectory.toPath();
        boolean copy = CopyProfile.requested() && Files.isRegularFile(root.resolve(CopyProfile.MARKER));
        if (!copy && !enabled()) {
            return;
        }
        if (!leavingWorld && (Boolean.getBoolean("mcaibuilder.managed")
                || client.level != null && Boolean.getBoolean("mcaibuilder.smoke"))) {
            try {
                Path stop = root.resolve(".mcaibuilder-stop");
                // A running integrated server may be ready before the client receives its level.
                if (Files.isRegularFile(stop) && client.level == null && client.getSingleplayerServer() != null) {
                    return;
                }
                if (Files.deleteIfExists(stop)) {
                    leaveAndSave(client);
                    return;
                }
            } catch (IOException error) {
                ForgeBridge.LOGGER.warn("Could not read the isolated save-and-exit request", error);
            }
        }
        if (!opened && client.screen instanceof TitleScreen title) {
            opened = true;
            if (copy) {
                try {
                    CopyProfile profile = CopyProfile.read(root);
                    LocalCopyFixture.remember(root, profile);
                    client.createWorldOpenFlows().loadLevel(title, CopyProfile.FOLDER);
                } catch (IOException | RuntimeException error) {
                    ForgeBridge.LOGGER.error("Could not verify the marked BMC4 world copy", error);
                }
            } else if (Files.isRegularFile(root.resolve("saves").resolve(WORLD_FOLDER).resolve("level.dat"))) {
                client.createWorldOpenFlows().loadLevel(title, WORLD_FOLDER);
            } else {
                createWorld(client);
            }
        }
        if (leavingWorld && client.level == null && client.getSingleplayerServer() == null) {
            client.stop();
            return;
        }
        if (!copy && client.level != null && !leavingWorld && Boolean.getBoolean("mcaibuilder.smoke")) {
            try {
                if (Files.deleteIfExists(root.resolve(".mcaibuilder-capture"))) {
                    Screenshot.grab(client.gameDirectory, "m1-wall-built.png", client.getMainRenderTarget(),
                            message -> ForgeBridge.LOGGER.info("M1 screenshot: {}", message.getString()));
                }
                if (Boolean.getBoolean("mcaibuilder.m2Smoke") || Boolean.getBoolean("mcaibuilder.m3Smoke")) {
                    pollPlayerEdit(client, root);
                }
            } catch (IOException error) {
                ForgeBridge.LOGGER.warn("M1 fixture signal failed", error);
            }
        }
    }

    private static void leaveAndSave(Minecraft client) {
        leavingWorld = true;
        if (client.level == null) {
            client.stop();
            return;
        }
        ForgeBridge.LOGGER.info("Isolated client leaving the world through Minecraft's normal save flow");
        client.level.disconnect();
        client.clearLevel(new GenericDirtMessageScreen(Component.translatable("menu.savingLevel")));
        client.setScreen(new TitleScreen());
    }

    private static void pollPlayerEdit(Minecraft client, Path root) throws IOException {
        Path signal = root.resolve(".mcaibuilder-player-place");
        MinecraftServer server = client.getSingleplayerServer();
        if (server == null || client.player == null || !Files.isRegularFile(signal)) {
            return;
        }
        String[] coordinates = Files.readString(signal).strip().split("\\s+");
        Files.delete(signal);
        if (coordinates.length != 3) {
            throw new IOException("Expected a test position");
        }
        BlockPos pos = new BlockPos(Integer.parseInt(coordinates[0]), Integer.parseInt(coordinates[1]),
                Integer.parseInt(coordinates[2]));
        BuildSite site = BuildSite.read(root.resolve("config").resolve("mcaibuilder.json"));
        if (site == null || !WORLD_NAME.equals(site.world()) || !site.canBuildAt(pos)
                || pos.getY() != site.spawn().getY()) {
            throw new IOException("Test edit is outside the fixture ground");
        }
        UUID player = client.player.getUUID();
        server.execute(() -> placeAsTestPlayer(server, root, player, pos));
    }

    private static void placeAsTestPlayer(MinecraftServer server, Path root, UUID id, BlockPos pos) {
        ServerPlayer player = server.getPlayerList().getPlayer(id);
        if (player == null || !WORLD_NAME.equals(server.getWorldData().getLevelName())
                || player.serverLevel() != server.overworld() || !server.overworld().getBlockState(pos).isAir()) {
            return;
        }
        player.teleportTo(player.serverLevel(), pos.getX() + 0.5, pos.getY(), pos.getZ() + 2.5, 180, 45);
        Vec3 eye = player.getEyePosition();
        Vec3 top = Vec3.atBottomCenterOf(pos).add(0, -0.01, 0);
        BlockHitResult hit = player.serverLevel().clip(new ClipContext(eye, top,
                ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, player));
        if (hit.getType() != HitResult.Type.BLOCK || !hit.getBlockPos().equals(pos.below())
                || hit.getDirection() != Direction.UP || eye.distanceToSqr(hit.getLocation()) > 4.5 * 4.5) {
            return;
        }
        ItemStack saved = player.getMainHandItem();
        ItemStack stone = new ItemStack(Blocks.COBBLESTONE);
        player.setItemInHand(InteractionHand.MAIN_HAND, stone);
        InteractionResult result;
        try {
            result = player.gameMode.useItemOn(player, player.serverLevel(), stone, InteractionHand.MAIN_HAND, hit);
        } finally {
            player.setItemInHand(InteractionHand.MAIN_HAND, saved);
        }
        JsonObject proof = new JsonObject();
        proof.addProperty("actor_type", "minecraft:player");
        proof.addProperty("method", "ServerPlayerGameMode.useItemOn");
        proof.addProperty("result", result.name());
        proof.add("world", Wire.position(pos));
        proof.add("actual", Wire.state(player.serverLevel().getBlockState(pos)));
        try {
            Files.writeString(root.resolve(".mcaibuilder-player-result.json"), proof.toString());
        } catch (IOException error) {
            ForgeBridge.LOGGER.warn("Could not record test player interaction", error);
        }
    }

    private static void createWorld(Minecraft client) {
        GameRules rules = new GameRules();
        rules.getRule(GameRules.RULE_RANDOMTICKING).set(0, null);
        rules.getRule(GameRules.RULE_DOMOBSPAWNING).set(false, null);
        rules.getRule(GameRules.RULE_DAYLIGHT).set(false, null);
        rules.getRule(GameRules.RULE_SPAWN_RADIUS).set(0, null);
        GameType mode = Boolean.getBoolean("mcaibuilder.survival") ? GameType.SURVIVAL : GameType.CREATIVE;
        LevelSettings settings = new LevelSettings(WORLD_NAME, mode, false,
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
