package dev.mcaibuilder.bridge;

import com.mojang.logging.LogUtils;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.player.Player;
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

@Mod(ForgeBridge.MOD_ID)
public final class ForgeBridge {
    public static final String MOD_ID = "mcaibuilder";
    static final Logger LOGGER = LogUtils.getLogger();

    private MinecraftServer server;
    private BridgeHttp http;
    private ConstructionService construction;
    private ArchitectService architect;
    private CopySites copy;

    public ForgeBridge() {
        MinecraftForge.EVENT_BUS.addListener(this::started);
        MinecraftForge.EVENT_BUS.addListener(this::registerCommands);
        MinecraftForge.EVENT_BUS.addListener(this::tick);
        MinecraftForge.EVENT_BUS.addListener(this::stopping);
        MinecraftForge.EVENT_BUS.addListener(EventPriority.LOWEST, this::playerPlace);
        MinecraftForge.EVENT_BUS.addListener(EventPriority.LOWEST, this::playerBreak);
    }

    private void registerCommands(RegisterCommandsEvent event) {
        ArchitectCommand.register(event, this::architectCommand);
    }

    private ConstructionService construction() {
        return copy == null ? construction : copy.construction();
    }

    private ArchitectService architect() {
        return copy == null ? architect : copy.architect();
    }

    private void architectCommand(ServerPlayer player, String message, boolean natural) {
        if (copy != null) {
            if (!copy.owns(player)) {
                throw new Wire.Problem(403, "site_forbidden", "需要由副本中的原存档玩家操作。");
            }
            String[] input = message.strip().split("\\s+", 2);
            if (!natural && input[0].equals("site")) {
                copy.command(player, input.length == 2 ? input[1] : "");
                return;
            }
            if (copy.architect() == null) {
                throw Wire.conflict("site_not_approved", "请先使用 /architect site 设置并批准施工区。");
            }
        }
        ArchitectService service = architect();
        if (service == null) {
            throw new Wire.Problem(503, "architect_disabled", "这个世界未启用建筑师。");
        }
        if (natural) {
            service.request(player, message);
        } else {
            service.receive(player, message);
        }
    }

    private void started(ServerStartedEvent event) {
        MinecraftServer game = event.getServer();
        try {
            if (CopyProfile.requested()) {
                CopyProfile profile = CopyProfile.read(FMLPaths.GAMEDIR.get());
                if (!profile.matches(game)) {
                    return;
                }
                CopySites sites = new CopySites(game, profile);
                http = new BridgeHttp(game, profile.port(), profile.token(), profile.architect().token(),
                        sites::construction, sites::architect, sites);
                copy = sites;
                server = game;
                LOGGER.info("Isolated world-copy Bridge listening on 127.0.0.1:{}", profile.port());
                return;
            }
            BuildSite site = BuildSite.read(FMLPaths.CONFIGDIR.get().resolve(MOD_ID + ".json"));
            if (site == null) {
                return;
            }
            if (!site.world().equals(game.getWorldData().getLevelName())) {
                LOGGER.info("M1 bridge inactive for world {}", game.getWorldData().getLevelName());
                return;
            }
            ServerLevel level = game.overworld();
            var palette = BuildPlan.palette();
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
            http = new BridgeHttp(game, site.port(), site.token(), chat == null ? null : site.architect().token(),
                    this::construction, this::architect, null);
            architect = chat;
            construction = service;
            server = game;
            LOGGER.info("M1 bridge listening on 127.0.0.1:{} in {}", site.port(), site.world());
        } catch (IOException | RuntimeException error) {
            LOGGER.error("Construction bridge could not start. Check the isolated instance configuration.", error);
        }
    }

    private void tick(TickEvent.ServerTickEvent event) {
        if (event.phase == TickEvent.Phase.END && event.getServer() == server) {
            ConstructionService current = construction();
            if (current != null) {
                current.tick();
            }
        }
    }

    private void playerPlace(BlockEvent.EntityPlaceEvent event) {
        ConstructionService current = construction();
        if (current == null || !(event.getLevel() instanceof ServerLevel level)
                || !(event.getEntity() instanceof Player player)) {
            return;
        }
        if (event instanceof BlockEvent.EntityMultiPlaceEvent multi) {
            multi.getReplacedBlockSnapshots().forEach(snapshot -> current.externalEdit(level, player, snapshot.getPos()));
        } else {
            current.externalEdit(level, player, event.getPos());
        }
    }

    private void playerBreak(BlockEvent.BreakEvent event) {
        ConstructionService current = construction();
        if (current != null && event.getLevel() instanceof ServerLevel level) {
            current.externalEdit(level, event.getPlayer(), event.getPos());
        }
    }

    private void stopping(ServerStoppingEvent event) {
        if (event.getServer() == server) {
            http.close();
            if (copy != null) {
                copy.deactivate();
            } else {
                construction.close();
            }
            http = null;
            copy = null;
            construction = null;
            architect = null;
            server = null;
        }
    }
}
