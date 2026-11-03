/**
 * The exact wordlist the tool ships with, all 397 of them: short, no
 * near-homophones, nothing over seven letters, so a code survives being read
 * out loud across a room. Kept in sync with toss/crypto.py.
 */
export const WORDS = (
  "amber anchor apple arrow aspen atlas bacon badge bagel banjo basil beach beacon beetle " +
  "bingo birch bison blade bloom board bolt bonsai boost brick bridge bronze brush bubble " +
  "cable cactus camel candle canoe canvas carbon cargo carpet cedar chalk charm cherry " +
  "chess chrome cider cinder circus citrus clamp cliff clover cobalt cocoa comet copper " +
  "coral cosmic cotton coyote crane crater crayon creek crisp crown crystal cue curry cyan " +
  "daisy dancer dapper dawn delta denim desert diamond diesel dingo dinner dock dolphin " +
  "domino donut dragon drift drum dune eagle east ember emerald engine escape ethos fable " +
  "falcon fancy feather fennel fern fiber fiddle finch flame flare flint float flora flute " +
  "foam forest fossil fox frost fuzzy gadget galaxy garden garlic gecko ginger glacier " +
  "glide globe glow gold gopher granite grape gravel green grid grotto guava gumbo gusto " +
  "hammer harbor harvest hazel heron hickory hollow honey hornet hostel hunter indigo ink " +
  "iris island ivory jade jaguar jasmine jelly jewel jigsaw jolly jungle juniper kayak " +
  "kernel kettle keypad kiosk kite koala lagoon lantern lapis lasso latte laurel lava ledge " +
  "lemon lentil lilac linen lion lobby locket lotus lumber lunar lychee magnet mango maple " +
  "marble marina marsh meadow melon mesa meteor mint mirror mocha mojave monsoon moss motel " +
  "mural museum nacho nebula nectar needle neon nest nickel noble nomad noodle north nugget " +
  "oasis ocean olive onyx opal orbit orchid otter oxide oyster pagoda palace panda papaya " +
  "pastel pebble pecan pelican pepper petal pewter phoenix piano picnic pigment pilot pine " +
  "pistol pivot pixel plasma plaza plum pocket pollen pond poppy portal potato prairie " +
  "prism puffin pumpkin purple quartz quiver rabbit radar rafter rain ranch rapid raven " +
  "reef relay ribbon ridge rifle ripple river robin rocket rodeo roster rubber ruby rust " +
  "saffron sage salmon sandal sapphire satin savanna scarlet scout sepia shadow shale shell " +
  "sierra silk silver siren sketch slate sloth smoke socket solar sonar sorbet spark spiral " +
  "spruce stable stamp statue steam stone storm straw studio sugar summit sunset surf swift " +
  "sycamore syrup tabby talon tandem tango tapir teal tempo tender thistle thunder tiger " +
  "timber tinder toffee tomato topaz torch totem toucan tower trail trellis tribe trophy " +
  "tulip tundra turbo turtle tusk ultra umber urban vanilla velvet venue verdant vessel " +
  "vinyl violet viper vista vivid vortex walnut wander wasabi waffle walrus wave weaver " +
  "whale wheat willow window winter wisp wolf wombat woven yarn yellow yodel yonder zebra " +
  "zenith zephyr zigzag zinc zodiac"
).split(" ");

/** Picked uniformly from the browser's own CSPRNG, the way the tool does it. */
export function makeCode(count = 4) {
  const picks = new Uint32Array(count);
  crypto.getRandomValues(picks);
  return [...picks].map((n) => WORDS[n % WORDS.length]);
}

export const BITS = Math.log2(WORDS.length) * 4;
