/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      borderRadius: { '3xl': '1.5rem' },
      boxShadow: {
        soft: '0 10px 35px rgba(0,0,0,.06)',
        card: '0 6px 22px rgba(0,0,0,.05)',
      },
      colors: {
        ui: {
          stroke: 'rgba(0,0,0,.08)',
        },
      },
    },
  },
  plugins: [],
};
