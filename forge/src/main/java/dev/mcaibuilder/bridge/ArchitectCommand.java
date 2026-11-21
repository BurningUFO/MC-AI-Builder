package dev.mcaibuilder.bridge;

import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import com.mojang.brigadier.exceptions.CommandSyntaxException;
import net.minecraft.commands.CommandSourceStack;
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
                            "[Architect] @Builder <需求>  |  /architect link  |  /architect approve <确认码>"));
                    return 1;
                })
                .then(Commands.literal("ask")
                        .then(Commands.argument("message", StringArgumentType.greedyString())
                                .executes(ctx -> run(ctx, service, true))))
                .then(Commands.argument("message", StringArgumentType.greedyString())
                        .executes(ctx -> run(ctx, service, false))));
    }

    private static int run(CommandContext<CommandSourceStack> ctx, Supplier<ArchitectService> service,
                           boolean naturalOnly) throws CommandSyntaxException {
        var player = ctx.getSource().getPlayerOrException();
        try {
            ArchitectService architect = service.get();
            if (architect == null) {
                throw new Wire.Problem(503, "architect_disabled", "这个世界未启用建筑师。");
            }
            String text = StringArgumentType.getString(ctx, "message");
            if (naturalOnly) {
                architect.request(player, text);
            } else {
                architect.receive(player, text);
            }
            return 1;
        } catch (Wire.Problem failure) {
            ctx.getSource().sendFailure(Component.literal(
                    "[Architect] " + failure.getMessage() + " [" + failure.code + "]"));
            return 0;
        }
    }
}
