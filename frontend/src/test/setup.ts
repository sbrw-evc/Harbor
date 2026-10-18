// What jsdom lacks and the components use: media queries (the theme, reduced motion) and the clipboard.
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn(() => Promise.resolve()) } });

// Downloads: the Blob handed to the browser is kept for the test to read; the click of the link does nothing in jsdom.
URL.createObjectURL = vi.fn(() => "blob:test");
URL.revokeObjectURL = vi.fn();
HTMLAnchorElement.prototype.click = vi.fn();

// The Web Animations API (auto-animate, the theme switch): an animation that is over at once.
Element.prototype.animate = vi.fn(() => ({ finished: Promise.resolve(), cancel() {}, addEventListener() {}, onfinish: null })) as never;

afterEach(() => {
  cleanup();
  sessionStorage.clear();
});
