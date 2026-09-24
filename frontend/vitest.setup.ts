import "@testing-library/jest-dom/vitest";

const store: Record<string, string> = {};
const localStorageMock = {
  getItem: (key: string) => store[key] ?? null,
  setItem: (key: string, value: string) => {
    store[key] = String(value);
  },
  removeItem: (key: string) => {
    delete store[key];
  },
  clear: () => {
    Object.keys(store).forEach((key) => delete store[key]);
  },
};

Object.defineProperty(globalThis, "localStorage", {
  value: localStorageMock,
  writable: true,
});

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver;
window.HTMLElement.prototype.scrollIntoView = function () {};

