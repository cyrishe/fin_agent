/* stockChartsPlugin.js
   A small plugin to load ECharts KLine + Indicators from /api/kline_ext
   Requires: 
     <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
*/

(function(global){
  // 全局对象, 也可改成 ES Module
  const StockChartsPlugin = {};

  // 默认option: candlestick + volume 2 yAxis, plus dynamic indicators
  function createBaseOption(){
    return {
      backgroundColor:'#1c1c1c',
      tooltip:{ trigger:'axis' },
      legend:{
        textStyle:{color:'#fff'},
        data:[]
      },
      xAxis:[
        {
          type:'category',
          data:[],
          axisLine:{ lineStyle:{ color:'#fff'} }
        }
      ],
      yAxis:[
        {
          scale:true,
          axisLine:{ lineStyle:{ color:'#fff'} },
          splitLine:{ show:false },
          name:"Price/Indicators"
        },
        {
          axisLine:{ lineStyle:{ color:'#fff'} },
          splitLine:{ show:false },
          name:"Volume",
          position:"right"
        }
      ],
      series:[
        {
          name:'Kline',
          type:'candlestick',
          data:[],
          itemStyle:{ color:'#f44', color0:'#0f4' },
          yAxisIndex:0
        },
        {
          name:'Volume',
          type:'bar',
          data:[],
          yAxisIndex:1
        }
      ]
    };
  }

  // parse & update chart with data from /api/kline_ext
  // data => { kline:[ [time,open,close,low,high,vol], ... ], indicators:{ MACD: [ [time,val], ... ], ...} }
  function updateChart(chart, data){
    const times = data.kline.map(item=>item[0]);
    const kData = data.kline.map(item=>[item[1],item[2],item[3],item[4]]); // open,close,low,high
    const volume= data.kline.map(item=> item[5] || 0);

    // base series
    // ECharts option => series: 0 => Kline, 1 => Volume
    // then dynamic indicators
    let seriesArr = [
      { name:'Kline', type:'candlestick', data:kData, yAxisIndex:0 },
      { name:'Volume', type:'bar', data:volume, yAxisIndex:1 }
    ];
    let legendData = ['Kline','Volume'];

    // parse data.indicators => each key => new line/bar series
    for(const indName in data.indicators){
      let arr = data.indicators[indName]; // e.g. [ [time,val], ... ]
      let values = arr.map(x=> x[1]);
      seriesArr.push({
        name: indName,
        type: 'line',
        data: values,
        yAxisIndex:0,
        showSymbol:false
      });
      legendData.push(indName);
    }

    chart.setOption({
      xAxis:[{ data: times }],
      legend:{ data: legendData },
      series: seriesArr
    });
  }

  // init a chart in container dom, returns chart instance
  function initChart(container){
    let chart = echarts.init(container);
    let baseOpt = createBaseOption();
    chart.setOption(baseOpt);
    return chart;
  }

  // core method: load chart data from api => update
  async function loadDataAndUpdate(chart, code, freq){
    let url = `/api/kline_ext?code=${code}&freq=${freq}`;
    let resp = await fetch(url);
    let data = await resp.json();
    updateChart(chart, data);
  }

  // publicly exposed
  // 1) create & fetch once
  StockChartsPlugin.createChart = async function(container, code, freq="minute"){
    let chart = initChart(container);
    await loadDataAndUpdate(chart, code, freq);
    return chart;
  }

  // 2) create & auto refresh
  StockChartsPlugin.createChartWithRefresh = function(container, code, freq="minute", intervalMs=10000){
    let chart = initChart(container);
    async function refresh(){
      await loadDataAndUpdate(chart, code, freq);
    }
    refresh();
    let timer = setInterval(refresh, intervalMs);
    return { chart, stop: ()=>clearInterval(timer) };
  }

  // 3) auto-scan .stock-chart elements
  StockChartsPlugin.loadAllCharts = function(){
    let chartDivs = document.querySelectorAll(".stock-chart");
    chartDivs.forEach(div=>{
      let code = div.getAttribute("data-code") || "sh600519";
      let freq = div.getAttribute("data-freq") || "minute";
      let refreshSec = parseInt(div.getAttribute("data-refresh")||"10",10)*1000;
      // create & refresh
      StockChartsPlugin.createChartWithRefresh(div, code, freq, refreshSec);
    });
  }

  // attach to window
  global.StockChartsPlugin = StockChartsPlugin;

})(window);

