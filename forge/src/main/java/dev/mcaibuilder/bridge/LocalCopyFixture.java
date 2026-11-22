package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.ListTag;
import net.minecraft.nbt.NbtIo;
import net.minecraft.nbt.Tag;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.phys.Vec3;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.event.entity.player.PlayerEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.registries.ForgeRegistries;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/** Local acceptance input and readback, enabled only for an explicitly marked copy test. */
@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT)
final class LocalCopyFixture {
    private static CompoundTag originalPlayer;

    private LocalCopyFixture() {}

    static void remember(Path root, CopyProfile profile) throws IOException {
        if (CopyProfile.fixture(root)) {
            originalPlayer = NbtIo.readCompressed(profile.world().resolve("level.dat").toFile())
                    .getCompound("Data").getCompound("Player");
        }
    }

    @SubscribeEvent
    public static void joined(PlayerEvent.PlayerLoggedInEvent event) {
        Path root = Minecraft.getInstance().gameDirectory.toPath();
        if (!CopyProfile.fixture(root) || originalPlayer == null || !(event.getEntity() instanceof ServerPlayer player)) {
            return;
        }
        try {
            CopyProfile profile = CopyProfile.read(root);
            var server = player.getServer();
            if (server == null || !profile.matches(server) || !profile.owns(server, player)) {
                return;
            }
            JsonObject proof = new JsonObject();
            proof.addProperty("saved_identity_matches", originalPlayer.hasUUID("UUID")
                    && originalPlayer.getUUID("UUID").equals(player.getUUID()));
            proof.addProperty("dimension_matches", originalPlayer.getString("Dimension")
                    .equals(player.serverLevel().dimension().location().toString()));
            ListTag pos = originalPlayer.getList("Pos", Tag.TAG_DOUBLE);
            proof.addProperty("position_matches", pos.size() == 3 && player.position()
                    .distanceToSqr(new Vec3(pos.getDouble(0), pos.getDouble(1), pos.getDouble(2))) < 0.01);
            ListTag inventory = player.getInventory().save(new ListTag());
            proof.addProperty("inventory_matches", inventory.equals(originalPlayer.getList("Inventory", Tag.TAG_COMPOUND)));
            proof.addProperty("inventory_stacks", inventory.size());
            proof.addProperty("game_mode_matches", player.gameMode.getGameModeForPlayer().getId()
                    == originalPlayer.getInt("playerGameType"));
            JobJournal.write(root.resolve(".mcaibuilder-copy-player-proof.json"), proof);
        } catch (IOException | RuntimeException error) {
            ForgeBridge.LOGGER.warn("Cannot record isolated owner load", error);
        }
    }

    @SubscribeEvent
    public static void tick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END) {
            return;
        }
        Minecraft client = Minecraft.getInstance();
        Path root = client.gameDirectory.toPath();
        var server = client.getSingleplayerServer();
        Path file = root.resolve(".mcaibuilder-copy-test-request");
        if (!CopyProfile.fixture(root) || server == null || client.player == null || !Files.isRegularFile(file)) {
            return;
        }
        try {
            JsonObject input = Wire.object(JsonParser.parseString(Files.readString(file)), "copy fixture");
            Files.delete(file);
            var id = client.player.getUUID();
            server.execute(() -> act(root, server.getPlayerList().getPlayer(id), input));
        } catch (IOException | RuntimeException error) {
            ForgeBridge.LOGGER.warn("Cannot read isolated copy test input", error);
        }
    }

    private static void act(Path root, ServerPlayer player, JsonObject input) {
        JsonObject result = new JsonObject();
        result.add("request", input.get("request"));
        try {
            CopyProfile profile = CopyProfile.read(root);
            if (player == null || !profile.matches(player.getServer()) || !profile.owns(player.getServer(), player)) {
                throw new IOException("Expected the saved player in the isolated copy");
            }
            switch (Wire.string(input.get("action"), "action")) {
                case "field" -> field(player, input);
                case "leave" -> leave(player);
                case "survey" -> result.add("terrain", survey(player));
                case "clear_grass" -> result.add("cleared", clearGrass(player, input));
                case "snapshot" -> result.add("blocks", snapshot(player, BuildSite.Box.read(input.get("area"), "area")));
                default -> throw new IOException("Unknown copy fixture action");
            }
            result.addProperty("success", true);
            result.add("position", Wire.position(player.blockPosition()));
        } catch (IOException | RuntimeException error) {
            result.addProperty("success", false);
            result.addProperty("error", error.getMessage());
        }
        try {
            JobJournal.write(root.resolve(".mcaibuilder-copy-test-response"), result);
        } catch (IOException error) {
            ForgeBridge.LOGGER.warn("Cannot record isolated terrain proof", error);
        }
    }

    private static void field(ServerPlayer player, JsonObject input) {
        ServerLevel level = player.getServer().overworld();
        BlockPos spawn = level.getSharedSpawnPos();
        BlockPos requested;
        if (input.has("position")) {
            requested = Wire.position(input.get("position"), "position");
        } else {
            BlockPos column = spawn.offset(240, 0, 0);
            level.getChunkAt(column);
            requested = level.getHeightmapPos(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, column);
        }
        if (!level.isInWorldBounds(requested) || requested.distSqr(spawn) > 1024 * 1024) {
            throw Wire.invalid("Fixture movement is limited to the copied spawn surroundings");
        }
        level.getChunkAt(requested);
        if (!BuildTerrain.canStand(level, requested)) {
            throw Wire.invalid("The copied terrain has no safe footing at the test-player destination");
        }
        player.teleportTo(level, requested.getX() + 0.5, requested.getY(), requested.getZ() + 0.5, 180, 0);
        player.gameMode.changeGameModeForPlayer(GameType.SURVIVAL);
        player.setInvulnerable(true);
    }

    private static void leave(ServerPlayer player) {
        ListTag pos = originalPlayer.getList("Pos", Tag.TAG_DOUBLE);
        ServerLevel end = player.getServer().getLevel(net.minecraft.world.level.Level.END);
        if (end == null || !originalPlayer.getString("Dimension").equals("minecraft:the_end") || pos.size() != 3) {
            throw Wire.invalid("Fixture leave requires the observed original End login");
        }
        player.gameMode.changeGameModeForPlayer(GameType.SPECTATOR);
        player.teleportTo(end, pos.getDouble(0), pos.getDouble(1), pos.getDouble(2), 0, 0);
    }

    private static JsonArray survey(ServerPlayer player) {
        ServerLevel level = player.serverLevel();
        BlockPos center = player.blockPosition();
        JsonArray terrain = new JsonArray();
        for (int x = center.getX() - 28; x <= center.getX() + 28; x++) {
            for (int z = center.getZ() - 28; z <= center.getZ() + 28; z++) {
                BlockPos column = new BlockPos(x, center.getY(), z);
                if (!level.hasChunkAt(column)) {
                    continue;
                }
                BlockPos top = level.getHeightmapPos(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, column);
                var floor = level.getBlockState(top.below());
                JsonObject row = new JsonObject();
                row.add("p", Wire.position(top));
                row.addProperty("stand", BuildTerrain.canStand(level, top));
                row.addProperty("anchor", BuildTerrain.NATURAL_SUPPORTS.contains(floor.getBlock()));
                row.addProperty("clear", level.getBlockState(top).isAir() && level.getBlockState(top.above()).isAir()
                        && level.getBlockState(top.above(2)).isAir());
                row.addProperty("constructed", !BuildTerrain.NATURAL_SUPPORTS.contains(floor.getBlock())
                        && !floor.isAir());
                terrain.add(row);
            }
        }
        return terrain;
    }

    private static JsonObject clearGrass(ServerPlayer player, JsonObject input) {
        // This local fixture simulates a test player preparing a plot before any construction review.
        ServerLevel level = player.serverLevel();
        BlockPos pos = Wire.position(input.get("position"), "position");
        if (!pos.equals(player.blockPosition()) || player.isSpectator() || player.isCreative()) {
            throw Wire.invalid("The survival test player must stand on the marked grass.");
        }
        var state = level.getBlockState(pos);
        boolean plant = state.is(Blocks.GRASS) || state.is(Blocks.TALL_GRASS)
                || state.is(Blocks.FERN) || state.is(Blocks.LARGE_FERN)
                || "minecraft:bush".equals(String.valueOf(ForgeRegistries.BLOCKS.getKey(state.getBlock())));
        if (!plant || !state.getCollisionShape(level, pos).isEmpty() || !level.mayInteract(player, pos)
                || !player.gameMode.destroyBlock(pos) || !level.getBlockState(pos).isAir()) {
            throw Wire.invalid("Only the listed non-solid plants can be cleared by the test player.");
        }
        JsonObject result = Wire.state(state);
        result.add("pos", Wire.position(pos));
        return result;
    }

    private static JsonArray snapshot(ServerPlayer player, BuildSite.Box area) {
        if (area.volume() > 24_000 || !area.contains(player.blockPosition())) {
            throw Wire.invalid("Fixture snapshot must be limited to the player's site");
        }
        ServerLevel level = player.serverLevel();
        JsonArray values = new JsonArray();
        for (BlockPos pos : BlockPos.betweenClosed(area.min(), area.max())) {
            if (!level.hasChunkAt(pos) || !level.isInWorldBounds(pos)) {
                throw Wire.conflict("site_not_loaded", "Fixture snapshot needs the loaded site");
            }
            var state = level.getBlockState(pos);
            if (!state.isAir()) {
                JsonObject row = Wire.state(state);
                row.add("pos", Wire.position(pos));
                values.add(row);
            }
        }
        return values;
    }
}
