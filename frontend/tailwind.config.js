/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["../templates/**/*.html", "../static/**/*.js"],
  theme: {
    extend: {
      colors: {
        cc2: {
          bg: "var(--cc2-bg)",
          card: "var(--cc2-card)",
          soft: "var(--cc2-card-soft)",
          text: "var(--cc2-text)",
          muted: "var(--cc2-muted)",
          primary: "var(--cc2-primary)",
          danger: "var(--cc2-danger)",
          warning: "var(--cc2-warning)",
          success: "var(--cc2-success)"
        }
      },
      fontFamily: {
        base: ["var(--font-base)"],
        heading: ["var(--font-heading)"],
        number: ["var(--font-number)"],
        button: ["var(--font-button)"]
      },
      borderRadius: {
        cc2: "var(--cc2-radius)"
      }
    }
  },
  plugins: []
};
