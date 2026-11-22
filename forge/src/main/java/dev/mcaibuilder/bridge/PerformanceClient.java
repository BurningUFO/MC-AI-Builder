package dev.mcaibuilder.bridge;

import net.minecraft.client.Minecraft;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;

@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT)
final class PerformanceClient {
    private static int frameSampleTick;

    @SubscribeEvent
    static void tick(TickEvent.ClientTickEvent event) {
        if (BuildPerformance.ENABLED && event.phase == TickEvent.Phase.END && ++frameSampleTick % 20 == 0) {
            BuildPerformance.clientFps = Minecraft.getInstance().getFps();
        }
    }
}
