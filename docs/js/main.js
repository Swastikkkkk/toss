import { initCodeGen } from "./codegen.js";
import { initCopy } from "./clipboard.js";
import { initKeyHold } from "./keyhold.js";
import { initMotion } from "./motion.js";

const gsap = window.gsap ?? null;
const ScrollTrigger = window.ScrollTrigger ?? null;
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

if (gsap && window.MotionPathPlugin) gsap.registerPlugin(window.MotionPathPlugin);

const context = { gsap, ScrollTrigger, reduced };

initMotion(context);
initKeyHold(context);
initCodeGen(context);
initCopy();
