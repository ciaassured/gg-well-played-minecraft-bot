package gg.wellplayed.jump.renderer.mixin;

import com.mojang.text2speech.Narrator;
import net.minecraft.client.GameNarrator;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

/** Prevents the renderer from loading a host text-to-speech native library. */
@Mixin(GameNarrator.class)
public abstract class GameNarratorMixin {
  @Redirect(
      method = "<init>",
      at =
          @At(
              value = "INVOKE",
              target =
                  "Lcom/mojang/text2speech/Narrator;getNarrator()Lcom/mojang/text2speech/Narrator;",
              remap = false))
  private Narrator jumpRenderer$useDisabledNarrator() {
    return Narrator.EMPTY;
  }
}
