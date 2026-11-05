// Popover attributes, unknown to the React 16 types; React passes them through.
import "react";

declare module "react" {
  interface HTMLAttributes<T> {
    popover?: "auto" | "manual";
  }
  interface ButtonHTMLAttributes<T> {
    popovertarget?: string;
  }
}
