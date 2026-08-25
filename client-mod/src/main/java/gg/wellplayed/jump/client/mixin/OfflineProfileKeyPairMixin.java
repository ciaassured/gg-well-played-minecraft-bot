package gg.wellplayed.jump.client.mixin;

import com.mojang.authlib.minecraft.UserApiService;
import com.mojang.authlib.yggdrasil.response.KeyPairResponse;
import net.minecraft.client.multiplayer.AccountProfileKeyPairManager;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

/** Avoids an authenticated profile-key request for the intentionally offline benchmark account. */
@Mixin(AccountProfileKeyPairManager.class)
public abstract class OfflineProfileKeyPairMixin {
  @Redirect(
      method = "fetchProfileKeyPair",
      at =
          @At(
              value = "INVOKE",
              target =
                  "Lcom/mojang/authlib/minecraft/UserApiService;getKeyPair()Lcom/mojang/authlib/yggdrasil/response/KeyPairResponse;",
              remap = false))
  private KeyPairResponse jumpBenchmark$useOfflineProfileKeyService(UserApiService userApiService) {
    UserApiService selected =
        Boolean.getBoolean("jump.client.offline") ? UserApiService.OFFLINE : userApiService;
    return selected.getKeyPair();
  }
}
