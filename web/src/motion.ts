// Motion: GSAP only where movement carries meaning. A screen settles in once
// when it changes; layovers are drawn along the route; today's bar becomes its
// ESTIMATE, so the change is seen, not just read; a stamp lands when a decision
// arrives while the screen is open. Control feedback, the phone sheet and
// disclosures stay CSS. Every hook does nothing under prefers-reduced-motion,
// and every tween clears its inline styles when it ends, so the resting layout
// is always the CSS one and a dropped frame never leaves a figure wrong.

import { useRef, type RefObject } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";

gsap.registerPlugin(useGSAP);

/** The CSS --ease-out, cubic-bezier(0.16, 1, 0.3, 1), is an exponential ease-out. */
const EASE = "expo.out";
const HIDDEN = "inset(0 100% 0 0)";
const SHOWN = "inset(0 0% 0 0)";

function reduced(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** The canvas's sections settle in, top to bottom, when the screen (path) changes. */
export function useScreenEnter(scope: RefObject<HTMLElement | null>, screen: string) {
  useGSAP(
    () => {
      if (reduced() || !scope.current) return;
      gsap.from(scope.current.children, {
        opacity: 0,
        y: 8,
        duration: 0.45,
        ease: EASE,
        stagger: { amount: 0.2 },
        clearProps: "opacity,transform",
      });
    },
    { scope, dependencies: [screen], revertOnUpdate: true },
  );
}

/** The strip and then each layover are drawn left to right, in travel order. */
export function useItineraryDraw(scope: RefObject<HTMLElement | null>, path: string) {
  useGSAP(
    () => {
      if (reduced() || !scope.current) return;
      const q = gsap.utils.selector(scope);
      const strip = q(".strip");
      const bars = q(".track i");
      const tl = gsap.timeline({ defaults: { ease: EASE } });
      if (strip.length) tl.fromTo(strip, { clipPath: HIDDEN }, { clipPath: SHOWN, duration: 0.8, clearProps: "clipPath" });
      if (bars.length) {
        tl.fromTo(
          bars,
          { clipPath: HIDDEN },
          { clipPath: SHOWN, duration: 0.6, stagger: 0.035, clearProps: "clipPath" },
          strip.length ? 0.12 : 0,
        );
      }
    },
    { scope, dependencies: [path], revertOnUpdate: true },
  );
}

/**
 * Today's bars are drawn, then each ESTIMATE bar starts at today's length and
 * moves to its own: the carbon copy visibly departs from the original. Each
 * ESTIMATE bar carries data-from (today's width) and data-to (its own).
 */
export function useEstimateDraw(scope: RefObject<HTMLElement | null>, proposal: string) {
  useGSAP(
    () => {
      if (reduced() || !scope.current) return;
      const q = gsap.utils.selector(scope);
      const today = q(".bar:not(.est)");
      const estimates = q(".bar.est") as HTMLElement[];
      const tl = gsap.timeline({ defaults: { ease: EASE } });
      if (today.length) {
        tl.fromTo(today, { clipPath: HIDDEN }, { clipPath: SHOWN, duration: 0.5, stagger: 0.08, clearProps: "clipPath" });
      }
      estimates.forEach((bar, i) => {
        const { from, to } = bar.dataset;
        if (!from || !to) return;
        tl.fromTo(bar, { width: from }, { width: to, duration: 0.9, clearProps: "width" }, 0.4 + i * 0.08);
      });
    },
    { scope, dependencies: [proposal], revertOnUpdate: true },
  );
}

/**
 * A stamp lands when its value changes for the same subject while the screen
 * is open (a decision arrived, a round finished). Never on first view and
 * never when the analyst switches to another subject: then nothing happened,
 * the screen just shows a different ticket. `target` selects inside `scope`;
 * without it the scope element itself lands.
 */
export function useStampLanding(
  scope: RefObject<HTMLElement | null>,
  subject: string,
  value: string | number,
  target?: string,
) {
  const last = useRef<{ subject: string; value: string | number } | null>(null);
  useGSAP(
    () => {
      const previous = last.current;
      last.current = { subject, value };
      if (!previous || previous.subject !== subject || previous.value === value) return;
      if (reduced() || !scope.current) return;
      const el = target ? scope.current.querySelector(target) : scope.current;
      if (!el) return;
      gsap.fromTo(
        el,
        { scale: 1.8, rotation: -6, opacity: 0 },
        { scale: 1, rotation: 0, opacity: 1, duration: 0.42, ease: "back.out(1.6)", clearProps: "transform,opacity" },
      );
    },
    { dependencies: [subject, value] },
  );
}
