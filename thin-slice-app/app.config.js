import 'dotenv/config';

export default ({ config }) => {
  return {
    ...config,
    extra: {
      ...config.extra,
      thinSliceServerUrl: process.env.EXPO_PUBLIC_THIN_SLICE_SERVER_URL || null,
      apiBaseUrl: process.env.EXPO_PUBLIC_API_BASE_URL || null,
      payfastReturnUrl: process.env.EXPO_PUBLIC_PAYFAST_RETURN_URL || null,
      payfastCancelUrl: process.env.EXPO_PUBLIC_PAYFAST_CANCEL_URL || null,
      payfastMode: process.env.EXPO_PUBLIC_PAYFAST_MODE || "sandbox",
      devJwt: process.env.EXPO_PUBLIC_DEV_JWT || null,
    },
  };
};
