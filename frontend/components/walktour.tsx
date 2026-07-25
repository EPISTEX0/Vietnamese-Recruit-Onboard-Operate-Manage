"use client";

import React, { useEffect, useRef, useCallback } from "react";
import Shepherd from "shepherd.js";
import type { StepOptions, Tour as ShepherdTour } from "shepherd.js";
import "shepherd.js/dist/css/shepherd.css";
import { WALKTOUR_STEPS } from "./walktour-steps";

const Tour = Shepherd.Tour;

declare global {
  interface Window {
    __startWalktour?: () => void;
    _walktour?: ShepherdTour;
  }
}

const LS_KEY = "vroom_walktour_seen";

interface WalktourProps {
  autoStart?: boolean;
}

export default function Walktour({ autoStart = false }: WalktourProps) {
  const tourRef = useRef<ShepherdTour | null>(null);
  const startedRef = useRef(false);

  const startTour = useCallback(() => {
    if (tourRef.current) {
      tourRef.current.start();
      startedRef.current = true;
    }
  }, []);

  useEffect(() => {
    const tour = new Tour({
      defaultStepOptions: {
        classes: "walktour-tooltip",
        scrollTo: { behavior: "smooth", block: "center" },
        cancelIcon: { enabled: true },
        arrow: true,
      },
      useModalOverlay: true,
    });

    // Register steps
    WALKTOUR_STEPS.forEach((stepOpts) => {
      tour.addStep({
        id: stepOpts.id,
        attachTo: stepOpts.attachTo,
        title: stepOpts.title,
        text: stepOpts.text,
        buttons: stepOpts.buttons,
      } as StepOptions);
    });

    // On complete/cancel: only set localStorage if tour actually started
    tour.on("complete", () => {
      if (startedRef.current && typeof window !== "undefined") {
        localStorage.setItem(LS_KEY, "true");
      }
    });
    tour.on("cancel", () => {
      if (startedRef.current && typeof window !== "undefined") {
        localStorage.setItem(LS_KEY, "true");
      }
    });

    // Expose tour instance for button action callbacks
    if (typeof window !== "undefined") {
      (window as any)._walktour = tour;
    }

    tourRef.current = tour;

    if (autoStart) {
      tour.start();
      startedRef.current = true;
    }

    return () => {
      // Don't fire complete/cancel events — just destroy
      tourRef.current = null;
      if (typeof window !== "undefined") {
        delete (window as any)._walktour;
      }
    };
  }, [autoStart]);

  // Expose startTour for parent via window
  useEffect(() => {
    if (typeof window !== "undefined") {
      (window as any).__startWalktour = startTour;
    }
    return () => {
      if (typeof window !== "undefined") {
        delete (window as any).__startWalktour;
      }
    };
  }, [startTour]);

  return null; // Invisible — renders overlay when tour starts
}
