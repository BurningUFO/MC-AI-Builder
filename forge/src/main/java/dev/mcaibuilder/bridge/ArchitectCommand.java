package dev.mcaibuilder.bridge;

import com.mojang.brigadier.arguments.StringArgumentType;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraftforge.event.RegisterCommandsEvent;

import java.util.function.Supplier;

final class ArchitectCommand {
    private ArchitectCommand() {}

    static void register(RegisterCommandsEvent event, Supplier<ArchitectService> service) {
        event.getDispatcher().register(Commands.literal("architect")
                .executes(ctx -> {
                    ctx.getSource().sendSystemMessage(Component.literal(
                            "[Architect] /architect <需求>  |  /architect approve <确认码>  |  /architect reject <确认码>"));
                    return 1;
                })
                .then(Commands.argument("message", StringArgumentType.greedyString()).executes(ctx -> {
                    var player = ctx.getSource().getPlayerOrException();
                    try {
                        ArchitectService architect = service.get();
                        if (architect == null) {
                            throw new Wire.Problem(503, "architect_disabled", "这个世界未启用建筑师。");
                        }
                        architect.receive(player, StringArgumentType.getString(ctx, "message"));
                        return 1;
                    } catch (Wire.Problem failure) {
                        ctx.getSource().sendFailure(Component.literal(
                                "[Architect] " + failure.getMessage() + " [" + failure.code + "]"));
                        return 0;
                    }
                })));
    }
}
