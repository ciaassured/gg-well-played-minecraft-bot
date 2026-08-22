package gg.wellplayed.jump.client.mixin;

import net.minecraft.client.sounds.SoundEngine;
import net.minecraft.client.sounds.SoundManager;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

/** Prevents OpenAL initialization while leaving the ordinary resource reload intact. */
@Mixin(SoundManager.class)
public abstract class SoundManagerMixin {
  @Redirect(
      method =
          "apply(Lnet/minecraft/client/sounds/SoundManager$Preparations;Lnet/minecraft/server/packs/resources/ResourceManager;Lnet/minecraft/util/profiling/ProfilerFiller;)V",
      at = @At(value = "INVOKE", target = "Lnet/minecraft/client/sounds/SoundEngine;reload()V"))
  private void jumpBenchmark$disableNativeAudio(SoundEngine soundEngine) {
    // The benchmark never consumes sound; starting OpenAL would only touch host audio.
  }
}
