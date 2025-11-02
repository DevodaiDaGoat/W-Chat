/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./client.html",
    "./script.js",
    "./style.css"
  ],
  theme: {
    extend: {
      colors: {
        'discord-dark': '#36393f',
        'discord-darker': '#2f3136',
        'discord-darkest': '#202225',
        'discord-light': '#dcddde',
        'discord-blue': '#7289da',
        'discord-red': '#f04747',
        'discord-green': '#43b581'
      },
      spacing: {
        '72': '18rem',
        '84': '21rem',
        '96': '24rem'
      },
      borderRadius: {
        'xl': '1rem'
      }
    }
  },
  plugins: [],
}