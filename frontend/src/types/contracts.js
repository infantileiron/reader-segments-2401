/** @typedef {{id:number,code:string,weekly_reading:number,completion:number,night_share:number,note:string}} Reader */
/** @typedef {{id:number,name:string,k:number,features:string[],state:string,result:object,purging?:boolean}} Segment */
/** @typedef {{id:number,segment_id:number,cluster_id:number,title:string,note:string,members:string[],status:string,purging?:boolean}} Outreach */
/** @typedef {{id:number,reader_code:string,state:string,impact:{segments:number[],outreaches:number[]},progress:{phase:string,done:number,total:number},report:object,error:string,created:string,updated:string}} Purge */
