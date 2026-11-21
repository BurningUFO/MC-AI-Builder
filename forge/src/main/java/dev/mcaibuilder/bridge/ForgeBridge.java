package dev.mcaibuilder.bridge;

import com.mojang.logging.LogUtils;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.block.Block;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.RegisterCommandsEvent;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.event.level.BlockEvent;
import net.minecraftforge.eventbus.api.EventPriority;
import net.minecraftforge.event.server.ServerStartedEvent;
import net.minecraftforge.event.server.ServerStoppingEvent;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.loading.FMLPaths;
import org.slf4j.Logger;

import java.io.IOException;
import java.util.Set;

@Mod(ForgeBridge.MOD_ID)
public final class ForgeBridge {
    public static final String MOD_ID = "mcaibuilder";
    static final Logger LOGGER = LogUtils.getLogger();

    private MinecraftServer server;
    private BridgeHttp http;
    private ConstructionService construction;
    private ArchitectService architect;

    public ForgeBridge() {
        MinecraftForge.EVENT_BUS.addListener(this::started);
        MinecraftForge.EVENT_BUS.addListener(this::registerCommands);
        MinecraftForge.EVENT_BUS.addListener(this::tick);
        MinecraftForge.EVENT_BUS.addListener(this::stopping);
        MinecraftForge.EVENT_BUS.addListener(EventPriority.LOWEST, this::playerPlace);
        MinecraftForge.EVENT_BUS.addListener(EventPriority.LOWEST, this::playerBreak);
    }

    private void registerCommands(RegisterCommandsEvent event) {
        ArchitectCommand.register(event, () -> architect);
    }

    private void started(ServerStartedEvent event) {
        MinecraftServer game = event.getServer();
        try {
            BuildSite site = BuildSite.read(FMLPaths.CONFIGDIR.get().resolve(MOD_ID + ".json"));
            if (site == null) {
                return;
            }
            if (!site.world().equals(game.getWorldData().getLevelName())) {
                LOGGER.info("M1 bridge inactive for world {}", game.getWorldData().getLevelName());
                return;
            }
            ServerLevel level = game.overworld();
            Set<Block> palette = BuildPlan.palette();
            ConstructionService service = new ConstructionService(level, site, palette,
                    new FakePlayersWorker(level, site, palette));
            ArchitectService chat = null;
            if (site.architect() != null) {
                try {
                    chat = new ArchitectService(level, site, service);
                } catch (IOException | RuntimeException error) {
                    LOGGER.error("Architect session storage could not be opened.", error);
                }
            }
            http = new BridgeHttp(game, site, service, chat);
            architect = chat;
            construction = service;
            server = game;
            LOGGER.info("M1 bridge listening on 127.0.0.1:{} in {}", site.port(), site.world());
        } catch (IOException | RuntimeException error) {
            LOGGER.error("M1 bridge could not start. Check the test instance configuration.", error);
        }
    }

    private void tick(TickEvent.ServerTickEvent event) {
        if (event.phase == TickEvent.Phase.END && event.getServer() == server) {
            construction.tick();
        }
    }

    private void playerPlace(BlockEvent.EntityPlaceEvent event) {
        if (construction == null || !(event.getLevel() instanceof ServerLevel level)
                || !(event.getEntity() instanceof Player player)) {
            return;
        }
        if (event instanceof BlockEvent.EntityMultiPlaceEvent multi) {
            multi.getReplacedBlockSnapshots().forEach(snapshot -> construction.externalEdit(level, player, snapshot.getPos()));
        } else {
            construction.externalEdit(level, player, event.getPos());
        }
    }

    private void playerBreak(BlockEvent.BreakEvent event) {
        if (construction != null && event.getLevel() instanceof ServerLevel level) {
            construction.externalEdit(level, event.getPlayer(), event.getPos());
        }
    }

    private void stopping(ServerStoppingEvent event) {
        if (event.getServer() == server) {
            http.close();
            construction.close();
            http = null;
            construction = null;
            architect = null;
            server = null;
        }
    }
}
