import { cn } from "@/lib/utils";

interface LoadingSpinnerProps {
  className?: string;
  size?: "sm" | "md" | "lg";
}

export const LoadingSpinner = ({ className, size = "md" }: LoadingSpinnerProps) => {
  const sizeClasses = {
    sm: "w-5 h-5 scale-[0.5]",
    md: "w-10 h-10",
    lg: "w-16 h-16 scale-[1.5]",
  };

  return (
    <div className={cn("segmented-spinner", sizeClasses[size], className)}>
      {[...Array(8)].map((_, i) => (
        <div key={i} />
      ))}
    </div>
  );
};
