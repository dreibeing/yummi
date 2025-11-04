import 'dotenv/config';

export default ({ config }) => {
  return {
    ...config,
    extra: {
      ...config.extra,
      thinSliceServerUrl: process.env.EXPO_PUBLIC_THIN_SLICE_SERVER_URL || null,
    },
  };
};

