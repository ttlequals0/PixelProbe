const path = require('path');
const TerserPlugin = require('terser-webpack-plugin');
const MiniCssExtractPlugin = require('mini-css-extract-plugin');
const CssMinimizerPlugin = require('css-minimizer-webpack-plugin');
const { WebpackManifestPlugin } = require('webpack-manifest-plugin');

module.exports = (env, argv) => {
  const isProduction = argv.mode === 'production';

  return {
    entry: {
      app: './static/js/app.js',
      auth: './static/js/auth.js',
      state: './static/js/state.js',
      styles: [
        './static/css/desktop.css',
        './static/css/mobile.css',
        './static/css/logo-styles.css'
      ]
    },
    output: {
      filename: isProduction ? 'js/[name].[contenthash:8].js' : 'js/[name].js',
      path: path.resolve(__dirname, 'static/dist'),
      clean: true,
      publicPath: '/static/dist/'
    },
    module: {
      rules: [
        {
          test: /\.css$/,
          use: [
            MiniCssExtractPlugin.loader,
            'css-loader'
          ]
        }
      ]
    },
    plugins: [
      new MiniCssExtractPlugin({
        filename: isProduction ? 'css/[name].[contenthash:8].css' : 'css/[name].css'
      }),
      new WebpackManifestPlugin({
        fileName: 'manifest.json',
        publicPath: '/static/dist/',
        generate: (seed, files) => {
          return files.reduce((manifest, file) => {
            const name = file.name.replace(/\.[a-f0-9]{8}\./, '.');
            manifest[name] = file.path;
            return manifest;
          }, seed);
        }
      })
    ],
    optimization: {
      minimize: isProduction,
      minimizer: [
        new TerserPlugin({
          terserOptions: {
            compress: {
              drop_console: false,
              pure_funcs: []
            },
            format: {
              comments: false
            }
          },
          extractComments: false
        }),
        new CssMinimizerPlugin()
      ]
    },
    devtool: isProduction ? false : 'source-map'
  };
};
