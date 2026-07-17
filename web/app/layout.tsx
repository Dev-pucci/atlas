import React from "react";

export const metadata = {
  title: "Atlas Annotator",
  description: "Hosted review for Atlas video annotations",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily: "Segoe UI, Arial, sans-serif",
          background: "#f4f5f7",
          color: "#1a1a2e",
        }}
      >
        {children}
      </body>
    </html>
  );
}
