import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./e2e',timeout:45000,expect:{timeout:12000},workers:1,use:{baseURL:'http://127.0.0.1:18765',viewport:{width:1440,height:1050},trace:'retain-on-failure'},outputDir:'../work/browser-test-results',reporter:[['list'],['json',{outputFile:'../evidence/browser-tests.json'}]]});
