import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, beforeEach } from "vitest";
import RuntimeShowcaseSection from "@/components/RuntimeShowcaseSection";

beforeEach(() => {
  window.IntersectionObserver = class IntersectionObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any;
});

describe("RuntimeShowcaseSection Component", () => {
  it("renders toggle buttons and default gVisor description", () => {
    render(<RuntimeShowcaseSection />);

    expect(screen.getByRole("button", { name: /gVisor Sandbox/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /MicroVM/i })).toBeInTheDocument();

    // Default gVisor specifications
    expect(screen.getByText("Kernel Syscall Interception (Sentry)")).toBeInTheDocument();
    expect(screen.getByText(/Go-based Sentry Kernel/i)).toBeInTheDocument();
  });

  it("switches description and technical specs when toggling to MicroVM", async () => {
    render(<RuntimeShowcaseSection />);

    const microVmButton = screen.getByRole("button", { name: /MicroVM/i });
    fireEvent.click(microVmButton);

    // MicroVM specifications
    expect(await screen.findByText("Hardware VT-x / AMD-V MicroVM Hypervisor")).toBeInTheDocument();
    expect(await screen.findByText(/Firecracker VMM \/ QEMU-lite \+ KVM/i)).toBeInTheDocument();
  });
});
