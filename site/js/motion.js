/**
 * Page motion. Everything here animates *from* a visible resting state, so if
 * GSAP never loads or the reader asked for reduced motion, the page is already
 * correct and nothing is left stranded at zero opacity.
 */
export function initMotion({ gsap, ScrollTrigger, reduced }) {
  const bar = document.querySelector("#topbar");
  if (bar) {
    const onScroll = () => bar.classList.toggle("is-stuck", window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  if (!gsap || reduced) return;

  // hero entrance, one orchestrated moment rather than scattered effects
  const intro = gsap.timeline({ defaults: { ease: "power3.out" } });
  intro
    .from("#hero-title", { opacity: 0, y: 16, duration: 0.6 })
    .from("#hero-lede", { opacity: 0, y: 12, duration: 0.55 }, 0.08)
    .from(".hero-actions > *", { opacity: 0, y: 10, duration: 0.45, stagger: 0.06 }, 0.18)
    .from("#demo", { opacity: 0, y: 18, scale: 0.98, duration: 0.6 }, 0.12);

  // the dashed wire draws itself without the paid DrawSVG plugin
  const path = document.querySelector("#wire-path");
  if (path && path.getTotalLength) {
    const len = path.getTotalLength();
    gsap.fromTo(path,
      { strokeDasharray: `0 ${len}` },
      { strokeDasharray: "2 6", duration: 0.7, delay: 0.35, ease: "power2.out" });
  }

  if (!ScrollTrigger) return;
  gsap.registerPlugin(ScrollTrigger);

  /* Only animate what is below the fold at load. Anything already on screen
     stays exactly as the HTML left it, so the first frame is complete and no
     element is ever sitting at zero opacity waiting to be scrolled to. */
  const belowFold = (el) => el.getBoundingClientRect().top > window.innerHeight * 0.92;

  const reveal = (el, vars, start) => {
    if (!belowFold(el)) return;
    gsap.from(el, {
      ...vars,
      scrollTrigger: { trigger: el, start, once: true },
    });
  };

  document.querySelectorAll("[data-reveal]").forEach((el) => {
    reveal(el, { opacity: 0, y: 18, duration: 0.55, ease: "power3.out" }, "top 88%");
  });

  document.querySelectorAll(".steps > li").forEach((el, i) => {
    reveal(el, {
      opacity: 0,
      x: -10,
      duration: 0.5,
      delay: (i % 4) * 0.05,
      ease: "power2.out",
    }, "top 90%");
  });
}
