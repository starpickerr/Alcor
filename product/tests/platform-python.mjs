export const python = process.env.ALCOR_BUSINESS_PYTHON || process.env.STELLA_BUSINESS_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
