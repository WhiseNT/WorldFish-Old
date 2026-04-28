const fs = require('fs');
const content = fs.readFileSync('D:/Programing/WorldFish/frontend/src/views/WorldBuilderView.vue', 'utf-8');
const template = content.split('<script>')[0];
const match = template.match(/class=\"([^\"]+)\"/g);
if (match) {
    const uniqueClasses = [...new Set(match.map(m => m.slice(7, -1)))];
    console.log(uniqueClasses.join(', '));
} else {
    console.log('No classes found');
}