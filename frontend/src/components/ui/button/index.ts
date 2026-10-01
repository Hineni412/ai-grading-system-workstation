import type { VariantProps } from "class-variance-authority"
import { cva } from "class-variance-authority"

export { default as Button } from "./Button.vue"

export const buttonVariants = cva(
  "inline-flex min-h-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-(--radius-control) text-sm font-medium transition-[background-color,border-color,color,transform] disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 shrink-0 [&_svg]:shrink-0 outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-2 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive",
  {
    variants: {
      variant: {
        default:
          "border border-transparent bg-primary text-primary-foreground hover:bg-(--color-accent-hover)",
        destructive:
          "bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40 dark:bg-destructive/60",
        outline:
          "border border-(--color-border-strong) bg-card text-(--color-text-secondary) hover:border-(--color-text-muted) hover:text-foreground",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        ghost:
          "hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        "default": "h-(--control-height-default) px-4 [--app-control-height:var(--control-height-default)] [--app-control-padding:16px]",
        "xs": "h-6 gap-1 px-2 text-xs [--app-control-height:24px] [--app-control-padding:8px] [--app-control-font:var(--font-size-caption)] [&_svg:not([class*='size-'])]:size-3",
        "sm": "h-(--control-height-small) gap-1.5 px-3 [--app-control-height:var(--control-height-small)] [--app-control-padding:12px] [--app-control-font:var(--font-size-dense)]",
        "lg": "h-(--control-height-large) px-5 [--app-control-height:var(--control-height-large)] [--app-control-padding:20px]",
        "icon": "size-(--control-height-default) [--app-control-height:var(--control-height-default)] [--app-control-padding:0px]",
        "icon-xs": "size-6 [--app-control-height:24px] [--app-control-padding:0px] [&_svg:not([class*='size-'])]:size-3",
        "icon-sm": "size-(--control-height-small) [--app-control-height:var(--control-height-small)] [--app-control-padding:0px]",
        "icon-lg": "size-(--control-height-large) [--app-control-height:var(--control-height-large)] [--app-control-padding:0px]",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
)
export type ButtonVariants = VariantProps<typeof buttonVariants>
