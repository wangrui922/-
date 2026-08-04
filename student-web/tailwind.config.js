/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef6ff',
          100: '#d9ecff',
          500: '#2f7cf6',
          600: '#1f63d6',
          700: '#1a4fac',
        },
      },
      // 移动端优先：正文 15px，行高放宽，适合小学生阅读
      fontSize: {
        base: ['15px', '1.7'],
      },
      maxWidth: {
        app: '520px',
      },
    },
  },
  plugins: [],
}
