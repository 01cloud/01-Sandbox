// Fallback ESLint Security Configuration for OpenSandbox Scanner
module.exports = [
  {
    files: ["**/*.js", "**/*.jsx", "**/*.ts", "**/*.tsx"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
    },
    rules: {
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-prototype-builtins": "warn",
    },
  },
];
