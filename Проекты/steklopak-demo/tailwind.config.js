/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,css}'],
  theme: {
    extend: {
      colors: {
        page: '#F3F5F7',
        card: '#FFFFFF',
        line: '#D8E0E8',
        ink: { DEFAULT: '#1E293B', muted: '#526071' },
        graphite: '#0F172A',
        accent: { DEFAULT: '#0369A1', hover: '#075985' },
      },
      fontFamily: {
        display: ['Manrope', 'Golos Text', 'Segoe UI', 'sans-serif'],
        body: ['Golos Text', 'Manrope', 'Segoe UI', 'sans-serif'],
      },
      letterSpacing: {
        tight2: '-0.025em',
      },
      maxWidth: {
        shell: '1240px',
      },
      transitionDuration: {
        350: '350ms',
        450: '450ms',
      },
    },
  },
  plugins: [],
};
