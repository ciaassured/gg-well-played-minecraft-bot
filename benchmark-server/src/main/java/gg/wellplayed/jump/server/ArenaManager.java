package gg.wellplayed.jump.server;

import gg.wellplayed.jump.server.core.ArenaGeometry;
import gg.wellplayed.jump.server.core.EpisodeController.Kinematics;
import org.bukkit.Bukkit;
import org.bukkit.GameMode;
import org.bukkit.GameRule;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.attribute.Attribute;
import org.bukkit.entity.Player;
import org.bukkit.event.player.PlayerTeleportEvent;
import org.bukkit.potion.PotionEffect;
import org.bukkit.util.BoundingBox;
import org.bukkit.util.Vector;

/** Applies arena and player mutations. Every method must run on Paper's main thread. */
final class ArenaManager {
  private static final float DEFAULT_WALK_SPEED = 0.2f;
  private static final double POSITION_TOLERANCE = 0.03;

  private final ArenaGeometry geometry;

  ArenaManager(ArenaGeometry geometry) {
    this.geometry = geometry;
  }

  double prepare(Player player, double gap) {
    requireMainThread();
    World world = player.getWorld();
    configureWorld(world);
    repairArena(world);

    player.setGameMode(GameMode.ADVENTURE);
    player.setWalkSpeed(DEFAULT_WALK_SPEED);
    player.setAllowFlight(false);
    player.setFlying(false);
    player.setSprinting(false);
    player.setSneaking(false);
    player.getInventory().clear();
    player.setFoodLevel(20);
    player.setSaturation(5.0f);
    player.setExhaustion(0.0f);
    player.setFireTicks(0);
    player.setFallDistance(0.0f);
    player.setFreezeTicks(0);
    for (PotionEffect effect : player.getActivePotionEffects()) {
      player.removePotionEffect(effect.getType());
    }
    var maxHealth = player.getAttribute(Attribute.MAX_HEALTH);
    if (maxHealth != null) {
      player.setHealth(maxHealth.getValue());
    }

    double spawnX = geometry.spawnCenterX(gap);
    Location spawn = new Location(world, spawnX, geometry.standingFeetY(), 0.5, -90.0f, 0.0f);
    player.setVelocity(new Vector());
    player.teleport(spawn, PlayerTeleportEvent.TeleportCause.PLUGIN);
    player.setVelocity(new Vector());
    return spawnX;
  }

  boolean isStable(Player player, double expectedSpawnX) {
    requireMainThread();
    Vector velocity = player.getVelocity();
    return player.isOnGround()
        && horizontalSpeedSquared(velocity) <= 1.0e-8
        && Math.abs(player.getX() - expectedSpawnX) <= POSITION_TOLERANCE
        && Math.abs(player.getZ() - 0.5) <= POSITION_TOLERANCE
        && Math.abs(player.getY() - geometry.standingFeetY()) <= POSITION_TOLERANCE;
  }

  double horizontalSpeedSquared(Player player) {
    requireMainThread();
    return horizontalSpeedSquared(player.getVelocity());
  }

  Kinematics observe(Player player) {
    requireMainThread();
    BoundingBox box = player.getBoundingBox();
    Vector velocity = player.getVelocity();
    return new Kinematics(
        box.getMaxX(),
        box.getMinX(),
        player.getY() - geometry.standingFeetY(),
        velocity.getY(),
        velocity.getX(),
        player.isOnGround());
  }

  private void configureWorld(World world) {
    world.setGameRule(GameRule.DO_MOB_SPAWNING, false);
    world.setGameRule(GameRule.DO_WEATHER_CYCLE, false);
    world.setGameRule(GameRule.DO_DAYLIGHT_CYCLE, false);
    world.setGameRule(GameRule.KEEP_INVENTORY, true);
    world.setStorm(false);
    world.setThundering(false);
    world.setTime(6000L);
  }

  private void repairArena(World world) {
    for (int x = geometry.floorMinX(); x <= geometry.floorMaxX(); x++) {
      for (int z = geometry.laneMinZ(); z <= geometry.laneMaxZ(); z++) {
        world.getBlockAt(x, geometry.floorY(), z).setType(Material.SMOOTH_STONE, false);
        for (int y = geometry.floorY() + 1; y <= geometry.floorY() + 4; y++) {
          world.getBlockAt(x, y, z).setType(Material.AIR, false);
        }
      }
    }
    for (int z = geometry.laneMinZ(); z <= geometry.laneMaxZ(); z++) {
      world
          .getBlockAt(geometry.wallX(), geometry.floorY() + 1, z)
          .setType(Material.SMOOTH_STONE, false);
    }
  }

  private static void requireMainThread() {
    if (!Bukkit.isPrimaryThread()) {
      throw new IllegalStateException("arena mutation/observation must run on Paper's main thread");
    }
  }

  private static double horizontalSpeedSquared(Vector velocity) {
    // A grounded vanilla player retains the gravity sentinel (-0.0784) in Y even while its
    // collision-resolved feet position is stationary. Reset readiness therefore uses the actual
    // lane motion and grounded position instead of requiring that internal Y value to be zero.
    return velocity.getX() * velocity.getX() + velocity.getZ() * velocity.getZ();
  }
}
