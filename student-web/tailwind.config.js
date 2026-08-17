/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: {
        // 主色：活力紫罗兰（高端 + 童趣的平衡）
        brand: {
          50: '#F3EEFF',
          100: '#E9E0FF',
          200: '#D6C4FF',
          300: '#B99BFF',
          400: '#9B72FF',
          500: '#7C5CFC',
          600: '#6A47E8',
          700: '#5635C4',
        },
        coral: { 400: '#FF8A65', 500: '#FF7043', 600: '#F4511E' },
        amber: { 300: '#FFD27A', 400: '#FFC24B', 500: '#FFB01F' },
        mint: { 400: '#34D9A0', 500: '#1FBF85', 600: '#0FA06B' },
        sky: { 400: '#4FC3F7', 500: '#29B6F6' },
        ink: { DEFAULT: '#2B2D42', soft: '#5A5C70', faint: '#9396AC' },
      },
      fontSize: {
        base: ['15px', '1.7'],
      },
      borderRadius: {
        '2.5xl': '1.25rem',
        '3xl': '1.5rem',
        '4xl': '2rem',
      },
      maxWidth: {
        app: '520px',
      },
      boxShadow: {
        soft: '0 10px 30px -8px rgba(124,92,252,0.20)',
        glow: '0 14px 34px -10px rgba(124,92,252,0.50)',
        card: '0 8px 24px -10px rgba(43,45,66,0.14)',
        'card-lg': '0 18px 44px -14px rgba(43,45,66,0.22)',
        float: '0 22px 48px -16px rgba(124,92,252,0.45)',
      },
      fontFamily: {
        rounded: ['PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', '-apple-system', 'sans-serif'],
      },
      keyframes: {
        float: {
          '0%,100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-8px)' },
        },
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(14px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        pop: {
          '0%': { opacity: '0', transform: 'scale(0.9)' },
          '60%': { transform: 'scale(1.03)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        'bounce-soft': {
          '0%,100%': { transform: 'translateY(0) scale(1)' },
          '50%': { transform: 'translateY(-5px) scale(1.06)' },
        },
        wiggle: {
          '0%,100%': { transform: 'rotate(0deg)' },
          '25%': { transform: 'rotate(-7deg)' },
          '75%': { transform: 'rotate(7deg)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        'pulse-ring': {
          '0%': { transform: 'scale(0.95)', opacity: '0.7' },
          '70%': { transform: 'scale(1.25)', opacity: '0' },
          '100%': { transform: 'scale(1.25)', opacity: '0' },
        },
      },
      animation: {
        float: 'float 4.5s ease-in-out infinite',
        'fade-up': 'fade-up .55s cubic-bezier(.22,1,.36,1) both',
        pop: 'pop .5s cubic-bezier(.22,1.2,.36,1) both',
        'bounce-soft': 'bounce-soft 1.8s ease-in-out infinite',
        wiggle: 'wiggle .7s ease-in-out',
        shimmer: 'shimmer 1.6s linear infinite',
        'pulse-ring': 'pulse-ring 2s cubic-bezier(.22,1,.36,1) infinite',
      },
    },
  },
  plugins: [],
}
